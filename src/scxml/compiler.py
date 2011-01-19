'''
This file is part of pyscxml.

    pyscxml is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pyscxml is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public License
    along with pyscxml.  If not, see <http://www.gnu.org/licenses/>.
    
    @author Johan Roxendal
    @contact: johan@roxendal.com
    
'''


from node import *
import sys, re, time
from xml.etree import ElementTree, ElementInclude
from functools import partial
from xml.sax.saxutils import unescape
import logging
from messaging import UrlGetter
from louie import dispatcher
from urllib2 import urlopen
import Queue
from eventprocessor import Event, SCXMLEventProcessor as Processor
from invoke import InvokeSCXML, InvokeSOAP, InvokePySCXMLServer, InvokeWrapper
from xml.parsers.expat import ExpatError

try: 
    from Cheetah.Template import Template as Tmpl
    def template(tmpl, namespace):
        return str(Tmpl(tmpl, namespace))
except ImportError:
    try:
        from django.template import Context, Template
        def template(tmpl, namespace):
            t = Template(tmpl)
            c = Context(namespace)
            return t.render(c)
    except ImportError:
        def template(tmpl, namespace):
            return tmpl % namespace
        


def prepend_ns(tag):
    return ns + tag

ns = "{http://www.w3.org/2005/07/scxml}"
pyscxml_ns = "{http://code.google.com/p/pyscxml}"
tagsForTraversal = ["scxml", "state", "parallel", "history", "final", "transition", "invoke", "onentry", "onexit"]
tagsForTraversal = map(prepend_ns, tagsForTraversal)


class Compiler(object):
    '''The class responsible for compiling the statemachine'''
    def __init__(self):
        self.doc = SCXMLDocument()
        self.doc.datamodel["_sessionid"] = "pyscxml_session_" + str(time.time())
        self.doc.datamodel["_response"] = Queue.Queue() 
        self.doc.datamodel["_x"] = {} 
        self.logger = logging.getLogger("pyscxml.Compiler." + str(id(self)))
        self.log_function = None
        self.strict_parse = False
    
    def parseAttr(self, elem, attr, default=None, is_list=False):
        if not elem.get(attr, elem.get(attr + "expr")):
            return default
        else:
            output = elem.get(attr) or self.getExprValue(elem.get(attr + "expr")) 
            return output if not is_list else output.split(" ")
        
    
    def do_execute_content(self, parent):
        '''
        @param parent: usually an xml Element containing executable children
        elements, but can also be any iterator of executable elements. 
        '''
        for node in parent:
            
            if node.tag == prepend_ns("log"):
                self.log_function(node.get("label"), self.getExprValue(node.get("expr")))
            elif node.tag == prepend_ns("raise"):
                eventName = node.get("event").split(".")
                self.interpreter.raiseFunction(eventName, self.parseData(node))
            elif node.tag == prepend_ns("send"):
                self.parseSend(node)
            elif node.tag == prepend_ns("cancel"):
                self.interpreter.cancel(self.parseAttr(node, "sendid"))
            elif node.tag == prepend_ns("assign"):
                
                if node.get("location") not in self.doc.datamodel:
                    self.logger.error("The location expression %s was not instantiated in the datamodel." % node.get("location"))
                    self.raiseError("error.execution.nameerror")
                    continue
                
                expression = node.get("expr") or node.text.strip()
                self.doc.datamodel[node.get("location")] = self.getExprValue(expression)
            elif node.tag == prepend_ns("script"):
                if node.get("src"):
                    self.execExpr(urlopen(node.get("src")).read())
                else:
                    self.execExpr(node.text)
                    
            elif node.tag == prepend_ns("if"):
                self.parseIf(node)
                
            elif node.tag == pyscxml_ns + "start_session":
                xml = None
                if node.find(prepend_ns("content")) != None:
                    xml = str(Template(node.find(prepend_ns("content")).text))
                elif node.get("expr"):
                    xml = self.getExprValue(node.get("expr"))
                elif self.parseAttr(node, "src"):
                    xml = urlopen(self.parseAttr(node, "src")).read()
                try:
                    multisession = self.doc.datamodel["_x"]["sessions"]
                    sm = multisession.make_session(self.parseAttr(node, "sessionid"), xml)
                    sm.start()
                except AssertionError:
                    self.logger.error("You supplied no xml for <pyscxml:start_session /> " 
                                        "and no default has been declared.")
                except KeyError:
                    self.logger.error("You can only use the pyscxml:start_session " 
                                      "element for documents in a MultiSession enviroment")
                    
                
                
            else:
                if self.strict_parse: 
                    raise Exception("PySCXML doesn't recognize the executabel content '%s'" % node.tag)
        
    
    def parseIf(self, node):
        def gen_prefixExec(itr):
            for elem in itr:
                if elem.tag not in map(prepend_ns, ["elseif", "else"]):
                    yield elem
                else:
                    break

        def gen_ifblock(ifnode):
            yield (ifnode, gen_prefixExec(ifnode))
            for elem in (x for x in ifnode if x.tag == prepend_ns("elseif") or x.tag == prepend_ns("else")):
                elemIndex = list(ifnode).index(elem)
                yield (elem, gen_prefixExec(ifnode[elemIndex+1:]))
        
        for ifNode, execList in gen_ifblock(node):
            if ifNode.tag == prepend_ns("else"):
                self.do_execute_content(execList)
            elif self.getExprValue(ifNode.get("cond")):
                self.do_execute_content(execList)
                break
    
    def parseData(self, child):
        '''
        Given a parent node, returns a data object corresponding to 
        its param child nodes, namelist attribute or content child element.
        '''
        output = {}
        for p in child.findall(prepend_ns("param")):
            expr = p.get("expr", p.get("name"))
            
            output[p.get("name")] = self.getExprValue(expr)
                
        
        if child.get("namelist"):
            for name in child.get("namelist").split(" "):
                output[name] = self.getExprValue(name)
        
        if child.find(prepend_ns("content")) != None:
            output["content"] = str(Template(child.find(prepend_ns("content")).text, self.doc.datamodel))
                    
        return output
    
    def parseSend(self, sendNode):

        type = self.parseAttr(sendNode, "type", "scxml")
        delay = self.parseAttr(sendNode, "delay", "0s")
        n, unit = re.search("(\d+)(\w+)", delay).groups()
        delay = float(n) if unit == "s" else float(n) / 1000
        event = self.parseAttr(sendNode, "event").split(".") if self.parseAttr(sendNode, "event") else None 
        target = self.parseAttr(sendNode, "target")
        data = self.parseData(sendNode)
        try:
            hints = eval(self.parseAttr(sendNode, "hints", "{}"))
            assert isinstance(hints, dict)
        except:
            self.logger.error("hints or hintsexpr malformed: %s" % hints)
            self.raiseError("error.execution.hints")
        
        if type == "scxml":
            if not target:
                self.interpreter.send(event, sendNode.get("id"), delay, data)
            elif target == "#_parent":
                self.interpreter.send(event, 
                                      sendNode.get("id"), 
                                      delay, 
                                      data, 
                                      self.interpreter.invokeId, 
                                      toQueue=self.doc.datamodel["_parent"])
            elif target == "#_internal":
                self.interpreter.raiseFunction(event, data)
                
            elif target.startswith("#_scxml_"): #sessionid
                sessionid = target.split("#_scxml_")[-1]
                try:
                    toQueue = self.doc.datamodel["_x"]["sessions"][sessionid].interpreter.externalQueue
                    self.interpreter.send(event, sendNode.get("id"), delay, data, toQueue=toQueue)
                except KeyError:
                    self.logger.error("The session '%s' is inaccessible." % sessionid)
                    self.raiseError("error.send.target")
                
            elif target[0] == "#" and target[1] != "_": # invokeid
                inv = self.doc.datamodel[target[1:]]
                if isinstance(inv, InvokePySCXMLServer):
                    inv.send(Processor.toxml(".".join(event), target, data, "", sendNode.get("id"), hints))
                else:
                    inv.send(event, sendNode.get("id"), delay, data)
            elif target[0] == "#" and target[1:] == "_response":
                self.logger.debug("sending to _response")
                self.doc.datamodel["_response"].put((self.parseData(sendNode), hints))
                
            elif target.startswith("http://"): # target is a remote scxml processor
                try:
                    origin = self.doc.datamodel["_ioprocessors"]["scxml"]
                except KeyError:
                    origin = ""
                eventXML = Processor.toxml(".".join(event), target, data, origin, sendNode.get("id", ""), hints)
                
                getter = self.getUrlGetter(target)
                
                getter.get_sync(target, {"_content" : eventXML})
                
            else:
                self.logger.error("The send target %s is malformed or unsupported by the platform." % target)
                self.raiseError("error.send.target")
            
            
        elif type == "basichttp":
            
            getter = self.getUrlGetter(target)
            
            getter.get_sync(target, data)
            
        elif type == "x-pyscxml-soap":
            self.doc.datamodel[target[1:]].send(event, sendNode.get("id"), delay, self.parseData(sendNode))
        elif type == "x-pyscxml-statemachine":
            try:
                evt_obj = Event(event, data)
                self.doc.datamodel[target].send(evt_obj)
            except Exception, e:
                self.logger.error("Exception while executing function at target: '%s'" % target)
                self.logger.error("%s: %s" % (type(e).__name__, str(e)) )
                self.raiseError("error.execution." + type(e).__name__.lower()) 
        
        # this is where to add parsing for more send types. 
        else:
            self.logger.error("The send type %s is invalid or unsupported by the platform" % type)
            self.raiseError("error.send.event")
    
    def getUrlGetter(self, target):
        getter = UrlGetter()
        
        dispatcher.connect(self.onHttpResult, UrlGetter.HTTP_RESULT, getter)
        dispatcher.connect(self.onHttpError, UrlGetter.HTTP_ERROR, getter)
        dispatcher.connect(self.onURLError, UrlGetter.URL_ERROR, getter)
        
        return getter

    def onHttpError(self, signal, error_code, source, **named ):
        self.logger.error("A code %s HTTP error has ocurred when trying to send to target %s" % (error_code, source))
        self.raiseError("error.communication")

    def onURLError(self, signal, sender):
        self.logger.error("The address %s is currently unavailable" % sender.url)
        self.raiseError("error.communication")
        
    def onHttpResult(self, signal, **named):
        self.logger.info("onHttpResult " + str(named))
    
    def raiseError(self, err):
        self.interpreter.raiseFunction(err.split("."), {}, type="platform")
    
    def parseXML(self, xmlStr, interpreterRef):
        self.interpreter = interpreterRef
#        xmlStr = removeDefaultNamespace(xmlStr)
        xmlStr = self.addDefaultNamespace(xmlStr)
        try:
            tree = ElementTree.fromstring(xmlStr)
        except ExpatError:
            
            xmlStr = "\n".join("%s %s" % (n, line) for n, line in enumerate(xmlStr.split("\n")))
            self.logger.error(xmlStr)
            raise
        ElementInclude.include(tree)
        self.strict_parse = tree.get("exmode", "lax") == "strict"
        preprocess(tree)
        self.setDatamodel(tree)
        
        for n, parent, node in iter_elems(tree):
            if parent != None and parent.get("id"):
                parentState = self.doc.getState(parent.get("id"))
            
            if node.tag == prepend_ns("scxml"):
                s = State(node.get("id"), None, n)
                s.initial = self.parseInitial(node)
                self.doc.name = node.get("name", "")
                    
                if node.find(prepend_ns("script")) != None:
                    self.execExpr(node.find(prepend_ns("script")).text)
                self.doc.rootState = s    
                
            elif node.tag == prepend_ns("state"):
                s = State(node.get("id"), parentState, n)
                s.initial = self.parseInitial(node)
                
                self.doc.addNode(s)
                parentState.addChild(s)
                
            elif node.tag == prepend_ns("parallel"):
                s = Parallel(node.get("id"), parentState, n)
                self.doc.addNode(s)
                parentState.addChild(s)
                
            elif node.tag == prepend_ns("final"):
                s = Final(node.get("id"), parentState, n)
                self.doc.addNode(s)
                
                if node.find(prepend_ns("donedata")) != None:
                    s.donedata = partial(self.parseData, node.find(prepend_ns("donedata")))
                else:
                    s.donedata = lambda:{}
                
                parentState.addFinal(s)
                
            elif node.tag == prepend_ns("history"):
                h = History(node.get("id"), parentState, node.get("type"), n)
                self.doc.addNode(h)
                parentState.addHistory(h)
                
            elif node.tag == prepend_ns("transition"):
                t = Transition(parentState)
                if node.get("target"):
                    t.target = node.get("target").split(" ")
                if node.get("event"):
                    t.event = map(lambda x: re.sub(r"(.*)\.\*$", r"\1", x).split("."), node.get("event").split(" "))
                if node.get("cond"):
                    t.cond = partial(self.getExprValue, node.get("cond"))    
                
                t.exe = partial(self.do_execute_content, node)
                parentState.addTransition(t)
    
            elif node.tag == prepend_ns("invoke"):
#                inv = self.parseInvoke(node, parentState.id)
#                inv_func = partial(self.parseInvoke, node, parentState.id)
                parentState.addInvoke(self.make_invoke_wrapper(node, parentState))
            elif node.tag == prepend_ns("onentry"):
                s = Onentry()
                s.exe = partial(self.do_execute_content, node)
                parentState.addOnentry(s)
            
            elif node.tag == prepend_ns("onexit"):
                s = Onexit()
                s.exe = partial(self.do_execute_content, node)
                parentState.addOnexit(s)
#            elif node.tag == "data":
                
    
        return self.doc

    def execExpr(self, expr):
        if not expr or not expr.strip(): return 
        try:
            expr = normalizeExpr(expr)
            exec expr in self.doc.datamodel
        except Exception, e:
            self.logger.error("Exception while executing expression in a script block: '%s'" % expr)
            self.logger.error("%s: %s" % (type(e).__name__, str(e)) )
            self.raiseError("error.execution." + type(e).__name__.lower())
                
    
    def getExprValue(self, expr):
        """These expression are always one-line, so their value is evaluated and returned."""
        if not expr: 
            return None
        expr = unescape(expr)
        
        try:
            return eval(expr, self.doc.datamodel)
        except Exception, e:
            self.logger.error("Exception while executing expression: '%s'" % expr)
            self.logger.error("%s: %s" % (type(e).__name__, str(e)) )
            self.raiseError("error.execution." + type(e).__name__.lower())
            return None
    
    
        
    def make_invoke_wrapper(self, node, parentId):
        invokeid = node.get("id")
        if not invokeid:
            invokeid = parentId + "." + self.doc.datamodel["_sessionid"]
            self.doc.datamodel[node.get("idlocation")] = invokeid
        
        
        def start_invoke(wrapper):
            inv = self.parseInvoke(node)
            wrapper.set_invoke(inv)
            self.doc.datamodel[inv.invokeid] = inv
            dispatcher.connect(self.onInvokeSignal, "init.invoke." + wrapper.invokeid, inv)
            dispatcher.connect(self.onInvokeSignal, "result.invoke." + wrapper.invokeid, inv)
            dispatcher.connect(self.onInvokeSignal, "error.communication.invoke." + wrapper.invokeid, inv)
            
            inv.start(self.interpreter.externalQueue)
            
        wrapper = InvokeWrapper(invokeid)
        wrapper.invoke = start_invoke
        
        return wrapper
        
        
        
    def onInvokeSignal(self, signal, sender, **kwargs):
        self.logger.debug("onInvokeSignal " + signal)
        if signal.startswith("error"):
            self.raiseError(signal)
        else:
            self.interpreter.send(signal, data=kwargs.get("data", {}), invokeid=sender.invokeid)  
    
    def parseInvoke(self, node):
        type = self.parseAttr(node, "type", "scxml")
        src = self.parseAttr(node, "src")
        if type == "scxml": # here's where we add more invoke types. 
                     
            inv = InvokeSCXML()
            if src:
                #TODO : should this should be asynchronous?
                inv.content = urlopen(src).read()
            elif node.find(prepend_ns("content")) != None:
                inv.content = str(Template(node.find(prepend_ns("content")).text, self.doc.datamodel))
            
        
        elif node.get("type") == "x-pyscxml-soap":
            inv = InvokeSOAP()
            inv.content = src
        elif node.get("type") == "x-pyscxml-responseserver":
            inv = InvokePySCXMLServer()
            inv.content = src
        
        inv.autoforward = node.get("autoforward", "false").lower() == "true"
        inv.type = type    
        
        if node.find(prepend_ns("finalize")) != None and len(node.find(prepend_ns("finalize"))) > 0:
            inv.finalize = partial(self.do_execute_content, node.find(prepend_ns("finalize")))
        elif node.find(prepend_ns("finalize")) != None and node.find(prepend_ns("param")) != None:
            paramList = node.findall(prepend_ns("param"))
            def f():
                for param in (p for p in paramList if not p.get("expr")): # get all param nodes without the expr attr
                    if param.get("name") in self.doc.datamodel["_event"].data:
                        self.doc.datamodel[param.get("name")] = self.doc.datamodel["_event"].data[param.get("name")]
            inv.finalize = f
        return inv

    def parseInitial(self, node):
        if node.get("initial"):
            return Initial(node.get("initial").split(" "))
        elif node.find(prepend_ns("initial")) is not None:
            transitionNode = node.find(prepend_ns("initial"))[0]
            assert transitionNode.get("target")
            initial = Initial(transitionNode.get("target").split(" "))
            initial.exe = partial(self.do_execute_content, transitionNode)
            return initial
        else: # has neither initial tag or attribute, so we'll make the first valid state a target instead.
            childNodes = filter(lambda x: x.tag in map(prepend_ns, ["state", "parallel", "final"]), list(node)) 
            if childNodes:
                return Initial([childNodes[0].get("id")])
            return None # leaf nodes have no initial 
    
    def setDatamodel(self, tree):
        
        for node in tree.getiterator(prepend_ns("data")):
            self.doc.datamodel[node.get("id")] = None
            if node.get("expr"):
                self.doc.datamodel[node.get("id")] = self.getExprValue(node.get("expr"))
            elif node.get("src"):
                try:
                    self.doc.datamodel[node.get("id")] = urlopen(node.get("src")).read()
                except Exception, e:
                    self.logger.error("Data src not found : '%s'\n" % node.get("src"))
                    self.logger.error("%s: %s\n" % (type(e).__name__, str(e)) )
                    raise e
                
    def addDefaultNamespace(self, xmlStr):
        if not "xmlns=" in re.search(r"<scxml.*?>", xmlStr, re.DOTALL).group():
            self.logger.warn("Your document lacks the correct "
                "default namespace declaration. It has been added for you, for parsing purposes.")
            return xmlStr.replace("<scxml", "<scxml xmlns='http://www.w3.org/2005/07/scxml'", 1)
        return xmlStr
        

def preprocess(tree):
    tree.set("id", "__main__")
    
    for n, parent, node in iter_elems(tree):
        if node.tag in map(prepend_ns, ["state", "parallel", "final", "invoke", "history"]) and not node.get("id"):
            id = parent.get("id") + "_%s_child_%s" % (node.tag, n)
            node.set('id',id)
            

def normalizeExpr(expr):
    # TODO: what happens if we have python strings in our script blocks with &gt; ?
    code = unescape(expr).strip("\n")
    
    firstLine = code.split("\n")[0]
    # how many whitespace chars in first line?
    indent_len = len(firstLine) - len(firstLine.lstrip())
    # indent left by indent_len chars
    code = "\n".join(map(lambda x:x[indent_len:], code.split("\n")))
    
    return code
    

def removeDefaultNamespace(xmlStr):
    return re.sub(r" xmlns=['\"].+?['\"]", "", xmlStr)


def iter_elems(tree):
    queue = [(None, tree)]
    n = 0
    while(len(queue) > 0):
        parent, child = queue.pop(0)
        yield (n, parent, child)
        n += 1 
        for elem in child:
            if elem.tag in tagsForTraversal:
                queue.append((child, elem))

    