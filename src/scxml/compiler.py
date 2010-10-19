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
from urllib2 import urlopen
import sys, re
from xml.etree import ElementTree, ElementInclude
from functools import partial
from xml.sax.saxutils import unescape
import logging

validExecTags = ["log", "script", "raise", "assign", "send", "cancel", "datamodel"]

def initLogger(instance):
    # create self.logger
    logger = logging.getLogger("pyscxml.compiler, id: %s" % id(instance))
    logger.setLevel(logging.INFO)
    
    # create console handler and set level to debug
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    
    # create formatter
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # add formatter to ch
    ch.setFormatter(formatter)
    
    # add ch to self.logger
    logger.addHandler(ch)

    return logger



def set_sid(node):
    #probably not always unique, let's rewrite this at some point
    id = node.parent.get("id") + "_%s_child" % node.tag
    node.set('id',id)

    
def getLogFunction(label, toPrint):
    if not label: label = "Log"
    def f():
        print "%s: %s" % (label, toPrint())
    return f
    

def preprocess(tree):
    tree.set("id", "__main__")
    for node in tree.getiterator():
        for child in node.getchildren():
            # set a reference to the parent of the node
            child.parent = node
            # make sure that states without ids gets an id assigned.
            if child.tag in ["state", "parallel", "final", "invoke", "history"] and not child.get("id"):
                set_sid(child)
            

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

class Compiler(object):
    
    def __init__(self):
        self.doc = SCXMLDocument()
        self.logger = initLogger(self)
    
    def parseAttr(self, elem, attr, is_list=False):
        if not elem.get(attr, elem.get(attr + "expr")):
            return
        else:
            output = elem.get(attr) if elem.get(attr) else self.getExprValue(elem.get(attr + "expr")) 
            return output if not is_list else output.split(" ")
        
    
    def getExecContent(self, node):
        fList = []
        for node in node.getchildren():
            
            if node.tag == "log":
                fList.append(getLogFunction(node.get("label"),  partial(self.getExprValue, node.get("expr"))))
            elif node.tag == "raise": 
                eventName = node.get("event").split(".")
                fList.append(partial(self.interpreter.raiseFunction, eventName))
            elif node.tag == "send":
    
                fList.append(self.parseSend(node))
            elif node.tag == "cancel":
                fList.append(partial(self.interpreter.cancel, node.get("sendid")))
            elif node.tag == "assign":
                expression = node.get("expr") if node.get("expr") else node.text
                # ugly scoping hack
                def utilF(loc=node.get("location"), expr=expression):
                    self.doc.datamodel[loc] = self.getExprValue(expr)
                fList.append(utilF)
            elif node.tag == "script":
                fList.append(self.getExprFunction(node.text))
            else:
                sys.exit("%s is either an invalid child of %s or it's not yet implemented" % (node.tag, node.parent.tag))
        
        # return a function that executes all the executable content of the node.
        def f():
            for func in fList:
                func()
        return f
    
    def appendParam(self, child, toObj):
        if child.find("param") != None:
            for p in child.findall("param"):
                expr = p.get("expr", p.get("name"))
                if p.get("expr"):
                    expr = p.get("expr")
                # not necesarily standars compliant (hard to tell):
                elif hasattr(self.doc.datamodel, p.get("name")):
                    expr = p.get("name")
                else:
                    expr = ""
    
                toObj[p.get("name")] = self.getExprValue(expr)
                
        
        if child.get("namelist"):
            for name in child.get("namelist").split(" "):
                toObj[name] = self.getExprValue(name)
        
        return toObj
    
    def parseSend(self, sendNode):
        type = sendNode.get("type") if sendNode.get("type") else "scxml"
        data = {}
        delay = int(self.parseAttr(sendNode, "delay")) if self.parseAttr(sendNode, "delay") else 0
        
        event = self.parseAttr(sendNode, "event").split(".")
        
        if not sendNode.get("target"):
            return partial(self.interpreter.send, event, sendNode.get("id"), delay)
        
        if sendNode.get("target")[0] == "#":
            target = sendNode.get("target")[1:]
            # this is where to add parsing for more send types. 
            #if(type == "scxml"):
            
            if(target == "_parent"):
                return partial(self.interpreter.send, event, sendNode.get("id"), delay, self.appendParam(sendNode, data), self.interpreter.invokeid, self.doc.datamodel["_parent"])
            else:
                def f():
                    self.doc.datamodel[target].send(event, sendNode.get("id"), delay, self.appendParam(sendNode, data))
                return f
        
        self.logger.error("Send parsing failed on :" + str(sendNode))
        return lambda:None
        
    
    
    def parseXML(self, xmlStr, interpreterRef):
        self.interpreter = interpreterRef
        xmlStr = removeDefaultNamespace(xmlStr)
        tree = ElementTree.fromstring(xmlStr)
        ElementInclude.include(tree)
    #    print ElementTree.tostring(tree)
        preprocess(tree)
        
        for n, node in enumerate(x for x in tree.getiterator() if x.tag not in validExecTags + ["datamodel"]):
            if hasattr(node, "parent") and node.parent.get("id"):
                parentState = self.doc.getState(node.parent.get("id"))
                
            
            if node.tag == "scxml":
                s = State(node.get("id"), None, n)
                s.initial = self.parseInitial(node)
                    
                if node.find("script") != None:
                    self.getExprFunction(node.find("script").text)()
                self.doc.rootState = s    
                
            elif node.tag == "state":
                s = State(node.get("id"), parentState, n)
                s.initial = self.parseInitial(node)
                
                self.doc.addNode(s)
                parentState.addChild(s)
                
            elif node.tag == "parallel":
                s = Parallel(node.get("id"), parentState, n)
                self.doc.addNode(s)
                parentState.addChild(s)
                
            elif node.tag == "final":
                s = Final(node.get("id"), parentState, n)
                self.doc.addNode(s)
                parentState.addFinal(s)
                
            elif node.tag == "history":
                h = History(node.get("id"), parentState, node.get("type"), n)
                self.doc.addNode(h)
                parentState.addHistory(h)
                
                
            elif node.tag == "transition":
                if node.parent.tag == "initial": continue
                t = Transition(parentState)
                if node.get("target"):
                    t.target = node.get("target").split(" ")
                if node.get("event"):
                    t.event = map(lambda x: re.sub(r"(.*)\.\*$", r"\1", x).split("."), node.get("event").split(" "))
                if node.get("cond"):
                    t.cond = partial(self.getExprValue, node.get("cond"))    
                
                t.exe = self.getExecContent(node)
                    
                parentState.addTransition(t)
    
            elif node.tag == "invoke":
                
                if node.get("type") == "scxml": # here's where we add more invoke types. 
                     
                    inv = InvokeSCXML(node.get("id"))
                    parentState.addInvoke(inv)
                    if node.get("src"):
                        inv.content = urlopen(node.get("src")).read()
                    elif node.get("content"):
                        inv.content = ElementTree.tostring(node.find("content/scxml"))
                    
                    inv.autoforward = bool(node.get("autoforward"))
                
                
                inv.type = node.get("type")   
                
                if node.find("finalize") != None and len(node.find("finalize")) > 0:
                    inv.finalize = self.getExecContent(node.find("finalize"))
                elif node.find("finalize") != None and node.find("param") != None:
                    def f():
                        for param in (p for p in node.findall("param") if not p.get("expr")): # get all param nodes without the expr attr
                            self.doc.datamodel[param.get("name")] = self.doc.datamodel["_event"].data[param.get("name")]
                    node.finalize = f
                
                           
            elif node.tag == "onentry":
                s = Onentry()
                s.exe = self.getExecContent(node)
                parentState.addOnentry(s)
            
            elif node.tag == "onexit":
                s = Onexit()
                s.exe = self.getExecContent(node)
                parentState.addOnexit(s)
            elif node.tag == "data":
                self.doc.datamodel[node.get("id")] = self.getExprValue(node.get("expr"))
                
    
        return self.doc

    def getExprFunction(self, expr):
        expr = normalizeExpr(expr)
        def f():
            exec expr in self.doc.datamodel
        return f
    
    def getExprValue(self, expr):
        """These expression are always one-line, so their value is evaluated and returned."""
        expr = unescape(expr)
        return eval(expr, self.doc.datamodel)

    def parseInitial(self, node):
        if node.get("initial"):
            return Initial(node.get("initial").split(" "))
        elif node.find("initial") is not None:
            transitionNode = node.find("initial")[0]
            assert transitionNode.get("target")
            initial = Initial(transitionNode.get("target").split(" "))
            initial.exe = self.getExecContent(transitionNode)
            return initial
        else: # has neither initial tag or attribute, so we'll make the first valid state a target instead.
            childNodes = filter(lambda x: x.tag in ["state", "parallel", "final"], list(node)) 
            if childNodes:
                return Initial([childNodes[0].get("id")])
            return None # leaf nodes have no initial 
    



if __name__ == '__main__':
    from pyscxml import StateMachine
    xml = open("../../unittest_xml/factorial.xml").read()
#    xml = open("../../resources/factorial.xml").read()
    sm = StateMachine(xml)
    sm.start()
    