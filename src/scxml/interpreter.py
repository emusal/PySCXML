 # -*- coding: utf-8 -*-
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
    
    This is an implementation of the interpreter algorithm described in the W3C standard document, 
    which can be found at:
    
    http://www.w3.org/TR/2009/WD-scxml-20091029/ 
    
    @author Johan Roxendal
    @author Torbjörn Lager
    @contact: johan@roxendal.com
'''


from node import *
import sys
import threading
import time
from datastructures import OrderedSet, List, Queue, BlockingQueue

true = True
false = False
null = None

g_continue = true 
configuration = OrderedSet()
previousConfiguration = OrderedSet()

statesToInvoke = OrderedSet()

internalQueue = Queue()
externalQueue = BlockingQueue()

historyValue = {}
dm = {}



"""procedure startEventLoop()

Upon entering the state machine, we take all internally enabled transitions, namely those that don't require an event and those that are triggered by internal events. (Internal events can only be generated by the state machine itself.) When all such transitions have been taken, we move to the main event loop, which is driven by external events.

procedure procedure startEventLoop():
   previousConfiguration = null;
   
   initialStepComplete = false;
   until(initialStepComplete):
         initialStepComplete = false;
         until(initialStepComplete):
            enabledTransitions = selectEventlessTransitions()
            if (enabledTransitions.isEmpty()): 
               internalEvent = internalQueue.dequeue()// this call returns immediately if no event is available
               if (internalEvent):
                  datamodel.assignValue("event", internalEvent)
                  enabledTransitions = selectTransitions(internalEvent)
               else:
                  initialStepComplete = true
 
            if (enabledTransitions):
                microstep(enabledTransitions.toList()
   
   mainEventLoop()

"""

def startEventLoop():
    """<p>This loop runs until we enter a top-level final state or an external entity cancels processing.  
    In either case 'continue' will be set to false (see EnterStates, below, for termination by entering
    a top-level final state.)</p><p>Each iteration through the loop consists of three main steps: 1) execute
    any &lt;invoke&gt; tags for atomic states that we entered on the last iteration through the loop 2) Wait
	for an external event and then execute any transitions that it triggers  3) Take any subsequent
	internally enabled transitions, namely those that don't require an event or that are triggered
	by an internal event. </p><p> This event loop thus enforces run-to-completion semantics, in which
	the system process an external event and then takes all the 'follow-up' transitions that
	the processing has enabled before looking for another external event.  For example, suppose
	that the <em>external</em> event queue contains events e1 and e2 and the machine is in state s1.  If processing
	e1 takes the machine to s2 and generates <em>internal</em> event e3, and s2 contains a transition t
	triggered by e3, the system is guaranteed to take t, no matter what transitions s2 or other
	states have that would be triggered by e2.  Note that this is true even though e2 was already in the
	external event queue when e3 was generated. In effect, the algorithm treats the processing of e3
	as finishing up the processing of e1. </p>"""
	
    previousConfiguration = null;
    initialStepComplete = false ;
    while not initialStepComplete:
        enabledTransitions = selectEventlessTransitions()
        if enabledTransitions.isEmpty():
            if internalQueue.isEmpty(): 
                initialStepComplete = true 
            else:
                internalEvent = internalQueue.dequeue()
                dm["event"] = internalEvent
                enabledTransitions = selectTransitions(internalEvent)
        if not enabledTransitions.isEmpty():
             microstep(enabledTransitions.toList())
    threading.Thread(target=mainEventLoop).start()


""" procedure mainEventLoop()
This loop runs until we enter a top-level final state or an external entity cancels processing. In either case 'continue' will be set to false (see EnterStates, below, for termination by entering a top-level final state.)

Each iteration through the loop consists of three main steps: 1) execute any <invoke> tags for atomic states that we entered on the last iteration through the loop 2) Wait for an external event and then execute any transitions that it triggers 3) Take any subsequent internally enabled transitions, namely those that don't require an event or that are triggered by an internal event.

This event loop thus enforces run-to-completion semantics, in which the system process an external event and then takes all the 'follow-up' transitions that the processing has enabled before looking for another external event. For example, suppose that the external event queue contains events e1 and e2 and the machine is in state s1. If processing e1 takes the machine to s2 and generates internal event e3, and s2 contains a transition t triggered by e3, the system is guaranteed to take t, no matter what transitions s2 or other states have that would be triggered by e2. Note that this is true even though e2 was already in the external event queue when e3 was generated. In effect, the algorithm treats the processing of e3 as finishing up the processing of e1. 

procedure procedure mainEventLoop():
   while(continue):
   
      for state in configuration.diff(previousConfiguration):
         if(isAtomic(state)):
            if state.invoke:
               state.invokeid = executeInvoke(state.invoke)
               datamodel.assignValue(state.invoke.attribute('idlocation'),state.invokeid)
      
      previousConfiguration = configuration
      externalEvent = externalQueue.dequeue() // this call blocks until an event is available
      datamodel.assignValue("event",externalEvent)
      enabledTransitions = selectTransitions(externalEvent)
      
      if (enabledTransitions):
         microstep(enabledTransitions.toList())
         
         // now take any newly enabled null transitions and any transitions triggered by internal events
         macroStepComplete = false;
         until(macroStepComplete):
            enabledTransitions = selectEventlessTransitions()
            if (enabledTransitions.isEmpty()): 
               internalEvent = internalQueue.dequeue()// this call returns immediately if no event is available
               if (internalEvent):
                  datamodel.assignValue("event", internalEvent)
                  enabledTransitions = selectTransitions(internalEvent)
               else:
                  macroStepComplete = true
 
            if (enabledTransitions):
                microstep(enabledTransitions.toList()
        
   // if we get here, we have reached a top-level final state or some external entity has set continue to false      
   exitInterpreter()  

"""

def mainEventLoop():
    global previousConfiguration
    global statesToInvoke
    while g_continue:

        for state in statesToInvoke:
            for inv in state.invoke:
                invoke(inv)
        statesToInvoke.clear()

        previousConfiguration = configuration
        
        externalEvent = externalQueue.dequeue() # this call blocks until an event is available
        
#        print "external event found: " + str(externalEvent.name)
        
        dm["event"] = externalEvent
        if hasattr(externalEvent, "invokeid"):
            for state in configuration:
                for inv in state.invoke:
                    if inv.invokeid == externalEvent.invokeid:  # event is the result of an <invoke> in this state
                        applyFinalize(inv, externalEvent)
                               
        enabledTransitions = selectTransitions(externalEvent)
        if not enabledTransitions.isEmpty():
            microstep(enabledTransitions.toList())
            
            # now take any newly enabled null transitions and any transitions triggered by internal events
            macroStepComplete = false 
            while not macroStepComplete:
                enabledTransitions = selectEventlessTransitions()
                if enabledTransitions.isEmpty():
                    if internalQueue.isEmpty(): 
                        macroStepComplete = true 
                    else:
                        internalEvent = internalQueue.dequeue()
                        dm["event"] = internalEvent
                        enabledTransitions = selectTransitions(internalEvent)
                if not enabledTransitions.isEmpty():
                     microstep(enabledTransitions.toList())
          
    # if we get here, we have reached a top-level final state or some external entity has set g_continue to false         
    exitInterpreter()  
     

""" procedure exitInterpreter()

The purpose of this procedure is to exit the current SCXML process by exiting all active states. If the machine is in a top-level final state, a Done event is generated. 

procedure exitInterpreter():
   inFinalState = false
   statesToExit = new Set(configuration)
   for s in statesToExit.toList().sort(exitOrder)
      for content in s.onexit:
         executeContent(content)
      for inv in s.invoke:
         cancelInvoke(inv)
      if (isFinalState(s) && isScxmlState(s.parent())):
         inFinalState = true
      configuration.delete(s)
   if (inFinalState):
      sendDoneEvent(???)
"""

def exitInterpreter():
    inFinalState = false 
    statesToExit = configuration.toList().sort(exitOrder)

    for s in statesToExit:
        for content in s.onexit:
            executeContent(content)
        for inv in s.invoke:
            cancelInvoke(inv)
        if isFinalState(s) and isScxmlState(s.parent):
            inFinalState = true 
        configuration.delete(s)
    if inFinalState:
        print "Exiting interpreter"
#       sendDoneEvent(???)


""" function selectEventlessTransitions()

This function selects all transitions that are enabled in the current configuration that do not require an event trigger. First test if the state has been preempted by a transition that has already been selected and that will cause the state to be exited when the transition is taken. If the state has not been preempted, find a transition with no 'event' attribute whose condition evaluates to true. If multiple matching transitions are present, take the first in document order. If none are present, search in the state's ancestors in ancestory order until one is found. As soon as such a transition is found, add it to enabledTransitions, and proceed to the next atomic state in the configuration. If no such transition is found in the state or its ancestors, proceed to the next state in the configuration. When all atomic states have been visited and transitions selected, return the set of enabled transitions.

function selectEventlessTransitions(event):
   enabledTransitions = new Set()
   atomicStates = configuration.toList().filter(isAtomicState)
   for state in atomicStates:
         if !(isPreempted(s, enabledTransitions)):
         loop: for s in [state].append(getProperAncestors(state,null)):
            for t in s.transition:
               if ( t.attribute('event') == null && conditionMatch(t)) 
               enabledTransitions.add(t)
               break loop
   return enabledTransitions

"""
def selectEventlessTransitions():
    enabledTransitions = OrderedSet()
    atomicStates = configuration.toList().filter(isAtomicState).sort(documentOrder)
    for state in atomicStates:
        # fixed type-o in algorithm
        if not isPreempted(state, enabledTransitions):
            done = false 
            for s in List([state]).append(getProperAncestors(state, null)):
                if done: break
                if not hasattr(s, "transition"): continue
                for t in s.transition:
                    if not t.event and conditionMatch(t): 
                        enabledTransitions.add(t)
                        done = true 
                        break
    return enabledTransitions


""" function selectTransitions(event)

The purpose of the selectTransitions() procedure is to collect the transitions that are enabled by this event in the current configuration.

Create an empty set of enabledTransitions. For each atomic state in the configuration, test if the state has been preempted by a transition that has already been selected and that will cause the state to be exited when the transition is taken. If the state has not been preempted, find a transition whose 'event' attribute matches event and whose condition evaluates to true. If multiple matching transitions are present, take the first in document order. If none are present, search in the state's ancestors in ancestry order until one is found. As soon as such a transition is found, add it to enabledTransitions, and proceed to the next atomic state in the configuration. If no such transition is found in the state or its ancestors, proceed to the next state in the configuration. When all atomic states have been visited and transitions selected, return the set of enabled transitions.

function selectTransitions(event):
   enabledTransitions = new Set()
   atomicStates = configuration.toList().filter(isAtomicState)
   for state in atomicStates:
>> Torbjorn: This needs to be fixed
      if (event.attribute('invokeid') != null && state.invokeid = event.invokeid):  //event is the result of an <invoke> in this state
         applyFinalize(state, event)
      if !(isPreempted(s, enabledTransitions)):
         loop: for s in [state].append(getProperAncestors(state,null)):
            for t in s.transition:
>>> Torbjorn: this needs to be fixed to handle multiple event designators
               if (t.attribute('event')!= null && isPrefix(t.attribute('event'), event.name) && conditionMatch(t)):
               enabledTransitions.add(t)
               break loop
   return enabledTransitions

"""

"""
For each atomic state in the configuration, check if the event is the result of an <invoke> in this state. If so, apply any <finalize> code in the state.
"""
            

def selectTransitions(event):
    enabledTransitions = OrderedSet()
    atomicStates = configuration.toList().filter(isAtomicState).sort(documentOrder)
    for state in atomicStates:
        if not isPreempted(state, enabledTransitions):
            done = false 
            for s in List([state]).append(getProperAncestors(state, null)):
                if done: break
                for t in s.transition:
                    if t.event and isPrefix(t.event, event.name) and conditionMatch(t):
                        enabledTransitions.add(t)
                        done = true 
                        break 
    return enabledTransitions


"""function isPreempted(s transitionList)

Return true if a transition T in transitionList exits an ancestor of state s. In this case, taking T will pull the state machine out of s and we say that it preempts the selection of a transition from s. Such preemption will occur only if s is a descendant of a parallel region and T exits that region. If we did not do this preemption check, we could end up in an illegal configuration, namely one in which there were multiple active states that were not all descendants of a common parallel ancestor.

function isPreempted(s transitionList):
   preempted = false
   for t in transitionList:
      if (t.attribute('target') != null):

         LCA = findLCA([t.parent()].append(getTargetStates(t)))
         if (isDescendant(s,LCA)):
            preempted = true
            break
   return preempted
"""
def isPreempted(s, transitionList):
    preempted = false 
    for t in transitionList:
        if t.target:
            LCA = findLCA(List([t.source]).append(getTargetStates(t.target)))
            if isDescendant(s,LCA):
                preempted = true 
                break
    return preempted


""" procedure microstep(enabledTransitions)

The purpose of the microstep procedure is to process the set of transitions enabled by an external event, an internal event, or by the presence or absence of certain values in the datamodel at the current point in time. The processing of the enabled transitions must be done in parallel ('lock step') in the sense that their source states must first be exited, then their actions must be executed, and finally their target states entered.

procedure microstep(enabledTransitions):
   exitStates(enabledTransitions)
   executeTransitionContent(enabledTransitions)
   enterStates(enabledTransitions)
"""

def microstep(enabledTransitions):
    exitStates(enabledTransitions)
    executeTransitionContent(enabledTransitions)
    enterStates(enabledTransitions)
    print "Config: {" + ", ".join([s.id for s in configuration if s.id != "__main__"]) + "}"


""" procedure exitStates(enabledTransitions)

Create an empty statesToExit set. For each transition t in enabledTransitions, if t is targetless then do nothing, else let LCA be the least common ancestor state of the source state and target states of t. Add to the statesToExit set all states in the configuration that are descendants of LCA. Convert the statesToExit set to a list and sort it in exitOrder.

For each state s in the list, if s has a deep history state h, set the history value of h to be the list of all atomic descendants of s that are members in the current configuration, else set its value to be the list of all immediate children of s that are members of the current configuration. Again for each state s in the list, first execute any onexit handlers, then cancel any ongoing invocations, and finally remove s from the current configuration.

procedure exitStates(enabledTransitions):
   statesToExit = new Set()
   for t in enabledTransitions:
      if (t.attribute('target') != null):
         LCA = findLCA([t.parent()].append(getTargetStates(t)))
         for s in configuration.toList():
            if (isDescendant(s,LCA)):
               statesToExit.add(s)
   statesToExit = statesToExit.toList().sort(exitOrder)
   for s in statesToExit:
      for h in s.history:
         f = (h.attribute('type') == "deep") ?
             lambda(s0): isAtomicState(s0) && isDescendant(s0,s) :
             lambda(s0): s0.parent() == s
         historyValue[h.attribute('id')] = configuration.toList().filter(f)
   for s in statesToExit:
      for content in s.onexit:
         executeContent(content)
      for inv in s.invoke:
         cancelInvoke(inv)
      configuration.delete(s)
"""
def exitStates(enabledTransitions):
    global statesToInvoke
    statesToExit = OrderedSet()
    for t in enabledTransitions:
        if t.target:
            LCA = findLCA(List([t.source]).append(getTargetStates(t.target)))
            for s in configuration:
                if isDescendant(s,LCA):
                    statesToExit.add(s)
    
    for s in statesToExit:
        statesToInvoke.delete(s)
        
    statesToExit = statesToExit.toList().sort(exitOrder)
    
    for s in statesToExit:
        for h in s.history:
            if h.type == "deep":
                f = lambda s0: isAtomicState(s0) and isDescendant(s0,s) 
            else:
                f = lambda s0: s0.parent == s
            historyValue[h.id] = configuration.toList().filter(f)
    for s in statesToExit:
        for content in s.onexit:
            executeContent(content)
        for inv in s.invoke:
            cancelInvoke(inv)
        configuration.delete(s)


def invoke(inv):
    print "Invoking: " + str(inv)
    
def cancelInvoke(inv):
    print "Cancelling: " + str(inv)
    

def executeTransitionContent(enabledTransitions):
    for t in enabledTransitions:
        executeContent(t)


def enterStates(enabledTransitions):
    global g_continue
    global statesToInvoke
    statesToEnter = OrderedSet()
    statesForDefaultEntry = OrderedSet()
    for t in enabledTransitions:
        if t.target:
            LCA = findLCA(List([t.source]).append(getTargetStates(t.target)))
            for s in getTargetStates(t.target):
                addStatesToEnter(s,LCA,statesToEnter,statesForDefaultEntry)
    
    for s in statesToEnter:
        statesToInvoke.add(s)

    statesToEnter = statesToEnter.toList().sort(enterOrder)
    
    for s in statesToEnter:
        configuration.add(s)
        for content in s.onentry:
            executeContent(content)
            # no support for this yet, plus it's clearly buggy (initial is a list)
#        if (s in statesForDefaultEntry):
#            executeContent(s.initial.transition.children())
        if isFinalState(s):
            parent = s.parent
            grandparent = parent.parent
            internalQueue.enqueue(InterpreterEvent(["done", "state", parent.id], {}))
            if isParallelState(grandparent):
                if getChildStates(grandparent).every(isInFinalState):
                    internalQueue.enqueue(InterpreterEvent(["done", "state", grandparent.id], {}))
    for s in configuration:
        if isFinalState(s) and isScxmlState(s.parent):
            g_continue = false ;


def addStatesToEnter(s,root,statesToEnter,statesForDefaultEntry):
    
    if isHistoryState(s):
        # i think that LCA should be changed for s and have done so
         if historyValue[s.id]:
             for s0 in historyValue[s.id]:
                  addStatesToEnter(s0, s, statesToEnter, statesForDefaultEntry)
         else:
             for t in s.transition:
                 for s0 in getTargetStates(t.target):
                     addStatesToEnter(s0, s, statesToEnter, statesForDefaultEntry)
    else:
        statesToEnter.add(s)
        if isParallelState(s):
            for child in getChildStates(s):
                addStatesToEnter(child,s,statesToEnter,statesForDefaultEntry)
        elif isCompoundState(s):
            for tState in getTargetStates(s.initial):
                statesForDefaultEntry.add(tState)
                addStatesToEnter(tState, s, statesToEnter, statesForDefaultEntry)
               # switched out the lines under for those over (getDefaultInitialState function doesn't exist).
        #         elif (isCompoundState(s)):
        #             statesForDefaultEntry.add(s)
        #             addStatesToEnter(getDefaultInitialState(s),s,statesToEnter,statesForDefaultEntry)
        for anc in getProperAncestors(s,root):
            
            statesToEnter.add(anc)
            if isParallelState(anc):
                for pChild in getChildStates(anc):
                    if not statesToEnter.toList().some(lambda s2: isDescendant(s2,pChild)):
                          addStatesToEnter(pChild,anc,statesToEnter,statesForDefaultEntry)


def isInFinalState(s):
    if isCompoundState(s):
        return getChildStates(s).some(lambda s: isFinalState(s) and configuration.member(s))
    elif isParallelState(s):
        return getChildStates(s).every(isInFinalState)
    else:
        return false 

def findLCA(stateList):
     for anc in getProperAncestors(stateList.head(), null):
        if stateList.tail().every(lambda s: isDescendant(s,anc)):
            return anc
            
def executeContent(obj):
    if callable(obj.exe):
        obj.exe()
        

def getTargetStates(targetIds):
    states = []
    for id in targetIds:
        states.append(doc.getState(id))
    return states

            
def getProperAncestors(state,root):
    ancestors = []
    while hasattr(state,'parent') and state.parent and state.parent != root:
        state = state.parent
        ancestors.append(state)
    
    return ancestors


def isDescendant(state1,state2):
    while hasattr(state1,'parent'):
        state1 = state1.parent
        if state1 == state2:
            return true 
    return false 


def getChildStates(state):
    return List(state.state + state.parallel + state.final + state.history)


def nameMatch(event,t):
    if not t.event:
        return false 
    else:
        return t.event == event["name"]
    

def conditionMatch(t):
    if not t.cond:
        return true 
    else:
        return t.cond(dm)


def isPrefix(eventList, event):
    if ["*"] in eventList: return true 
    def prefixList(l1, l2):
        if len(l1) > len(l2): return false 
        for tup in zip(l1, l2):
            if tup[0] != tup[1]:
                return false 
        return true 
    
    for elem in eventList:
        if prefixList(elem, event):
            return true 
    return false 


##
## Various tests for states
##

def isParallelState(s):
    return isinstance(s,Parallel)


def isFinalState(s):
    return isinstance(s,Final)


def isHistoryState(s):
    return isinstance(s,History)


def isScxmlState(s):
    return s.parent == null


def isAtomicState(s):
    return isinstance(s, Final) or (isinstance(s,SCXMLNode) and s.state == [] and s.parallel == [] and s.final == [])


def isCompoundState(s):
    return isinstance(s,SCXMLNode) and (s.state != [] or s.parallel != [] or s.final != [])


##
## Sorting orders
##

def documentOrder(s1,s2):
    if s1.n - s2.n:
        return 1
    else:
        return -1


def enterOrder(s1,s2):
    if isDescendant(s1,s2):
        return 1
    elif isDescendant(s2,s1):
        return -1
    else:
        return documentOrder(s1,s2)


def exitOrder(s1,s2):
    if isDescendant(s1,s2):
        return -1
    elif isDescendant(s2,s1):
        return 1
    else:
        return documentOrder(s2,s1)


def In(name):
    return OrderedSet(map(lambda x: x.id, configuration)).member(name)

timerDict = {}
def send(name,sendid="", data={},delay=0):
    """Spawns a new thread that sends an event after a specified time, in seconds"""
    if type(name) == str: name = name.split(".")
    
    if delay == 0: 
        sendFunction(name, data)
        return
    timer = threading.Timer(delay, sendFunction, args=(name, data))
    if sendid:
        timerDict[sendid] = timer
    timer.start()
    
def sendFunction(name, data):
    externalQueue.enqueue(InterpreterEvent(name, data))

def cancel(sendid):
    if timerDict.has_key(sendid):
        timerDict[sendid].cancel()
        del timerDict[sendid]
        

def raiseFunction(event):
    internalQueue.enqueue(InterpreterEvent(event, {}))

def interpret(document):
    '''Initializes the interpreter given an SCXMLDocument instance'''
    
    global doc
    global dm
    doc = document
    
    dm = doc.datamodel
    
    transition = Transition(document.rootState)
    transition.target = document.rootState.initial
    
    microstep([transition])

    startEventLoop()

    
    
class InterpreterEvent(object):
    def __init__(self, name, data):
        self.name = name
        self.data = data
        
    def __str__(self):
        return "InterpreterEvent name='%s'" % self.name  
    
    
if __name__ == "__main__":

    import compiler as comp 
    compiler = comp.Compiler()
    compiler.registerSend(send)
    compiler.registerRaise(raiseFunction)
    compiler.registerCancel(cancel)
    
    comp.In = In

#    xml = open("../../unittest_xml/colors.xml").read()
    xml = open("../../resources/colors.xml").read()
    
    interpret(compiler.parseXML(xml))
    
    
#    send("e1")
#    send("pause")
#    send("resume")
#    send("terminate")
    
#    send("e1", delay=1)
#    send("unlock_2", delay=2)
#    send("open", delay=3)
    
    
    

