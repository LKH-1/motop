#!/usr/bin/env python
##
# Tart Database Operations
# "Top" Clone for MongoDB
#
# @author  Emre Hasegeli <emre.hasegeli@tart.com.tr>
# @date    2012-05-19
##

from __future__ import print_function
try:
    import __builtin__
    __builtin__.input = __builtin__.raw_input
except ImportError: pass

import sys
import os
import tty
import termios
import select
import json
from bson import json_util
from time import sleep

try:
    from ConfigParser import ConfigParser
except ImportError:
    from configparser import ConfigParser

class Value (int):
    def __str__ (self):
        if self > 10 ** 12:
            return str (int (round (self / 10 ** 12))) + 'T'
        if self > 10 ** 9:
            return str (int (round (self / 10 ** 9))) + 'G'
        if self > 10 ** 6:
            return str (int (round (self / 10 ** 6))) + 'M'
        if self > 10 ** 3:
            return str (int (round (self / 10 ** 3))) + 'K'
        return int.__str__ (self)

class Printable:
    def line (self): pass
    def sortOrder (self): pass

class ListPrinter:
    def __init__ (self, columnHeaders, descendingOrder = False, maxLine = None):
        self.__columnHeaders = columnHeaders
        self.__columnWidths = [len (columnHeader) + 2 for columnHeader in self.__columnHeaders]
        self.__maxLine = maxLine
        self.__descendingOrder = descendingOrder

    def reset (self, printables):
        self.__printables = sorted (printables, key = lambda printable: printable.sortOrder (), reverse = self.__descendingOrder)
        if self.__maxLine:
            self.__printables = self.__printables [:self.__maxLine]
        for printable in self.__printables:
            assert isinstance (printable, Printable)

    def printLines (self):
        for index, columnHeader in enumerate (self.__columnHeaders):
            print (columnHeader.ljust (self.__columnWidths [index]), end = '')
        print ()
        for printable in self.__printables:
            for index, cell in enumerate (printable.line ()):
                assert isinstance (cell, str)
                if len (cell) + 2 > self.__columnWidths [index]:
                    self.__columnWidths [index] = len (cell) + 2
                print (cell.ljust (self.__columnWidths [index]), end = '')
            print ()

    def getLine (self, line):
        for printable in self.__printables:
            printableLine = printable.line ()
            different = False
            index = 0
            while not different and len (line) > index:
                if printableLine [index] != line [index]:
                    different = True
                index += 1
            if not different:
                return printable

class Operation (Printable):
    def __init__ (self, server, opid):
        self.__server = server
        self.__opid = opid

    def getServer (self):
        return self.__server

    def sortOrder (self):
        return -1 * self.__opid

    def line (self):
        cells = []
        cells.append (str (self.__server))
        cells.append (str (self.__opid))
        return cells

    def kill (self):
        return self.__server.killOperation (self.__opid)

class Query (Operation):
    def __init__ (self, server, opid, namespace, body, duration = None):
        Operation.__init__ (self, server, opid)
        self.__namespace = namespace
        self.__body = body
        self.__duration = duration

    def sortOrder (self):
        return self.__duration if self.__duration else 0

    listPrinter = ListPrinter (['Server', 'OpId', 'Namespace', 'Sec', 'Query'], descendingOrder = True, maxLine = 30)
    def line (self):
        cells = Operation.line (self)
        cells.append (str (self.__namespace))
        cells.append (str (self.__duration))
        cells.append (json.dumps (self.__body, default = json_util.default) [:80])
        return cells

    def printExplain (self):
        if self.__namespace:
            server = self.getServer ()
            databaseName, collectionName = self.__namespace.split ('.', 1)
            explainOutput = server.explainQuery (databaseName, collectionName, self.__body)
            print ('Cursor:', explainOutput ['cursor'])
            print ('Indexes:', end = '')
            for index in explainOutput ['indexBounds']:
                print (index, end = '')
            print ()
            print ('IndexOnly:', explainOutput ['indexOnly'])
            print ('MultiKey:', explainOutput ['isMultiKey'])
            print ('Miliseconds:', explainOutput ['millis'])
            print ('Documents:', explainOutput ['n'])
            print ('ChunkSkips:', explainOutput ['nChunkSkips'])
            print ('Yields:', explainOutput ['nYields'])
            print ('Scanned:', explainOutput ['nscanned'])
            print ('ScannedObjects:', explainOutput ['nscannedObjects'])
            if 'scanAndOrder' in explainOutput:
                print ('ScanAndOrder:', explainOutput ['scanAndOrder'])
            print ('Query:', json.dumps (self.__body, default = json_util.default, sort_keys = True, indent = 4))
            return True
        return False

class Server (Printable):
    def __init__ (self, name, address):
        from pymongo import Connection
        assert len (name) < 14
        self.__name = name
        self.__address = address
        self.__connection = Connection (address)
        self.__operationCount = 0
        self.__flushCount = 0

    def sortOrder (self):
        return self.__name

    def __getOperationCountChange (self, operationCounts):
        oldOperationCount = self.__operationCount
        self.__operationCount = sum ([value for key, value in operationCounts.items ()])
        return self.__operationCount - oldOperationCount

    def __getFlushCountChange (self, flushCount):
        oldFlushCount = self.__flushCount
        self.__flushCount = flushCount
        return self.__flushCount - oldFlushCount

    listPrinter = ListPrinter (['Server', 'QPS', 'Clients', 'Queue', 'Flushes', 'Connections', 'Memory'])
    def line (self):
        serverStatus = self.__connection.admin.command ('serverStatus')
        currentConnection = Value (serverStatus ['connections'] ['current'])
        totalConnection = Value (serverStatus ['connections'] ['available'] + serverStatus ['connections'] ['current'])
        residentMem = Value (serverStatus ['mem'] ['resident'])
        mappedMem = Value (serverStatus ['mem'] ['mapped'])
        cells = []
        cells.append (str (self))
        cells.append (str (Value (self.__getOperationCountChange (serverStatus ['opcounters']))))
        cells.append (str (Value (serverStatus ['globalLock'] ['activeClients'] ['total'])))
        cells.append (str (Value (serverStatus ['globalLock'] ['currentQueue'] ['total'])))
        cells.append (str (Value (self.__getFlushCountChange (serverStatus ['backgroundFlushing'] ['flushes']))))
        cells.append (str (currentConnection) + ' / ' + str (totalConnection))
        cells.append (str (residentMem) + ' / ' + str (mappedMem))
        return cells

    def explainQuery (self, databaseName, collectionName, query):
        database = getattr (self.__connection, databaseName)
        collection = getattr (database, collectionName)
        cursor = collection.find (query)
        return cursor.explain ()

    def currentOperations (self):
        for op in self.__connection.admin.current_op () ['inprog']:
            if op ['op'] == 'query':
                if 'secs_running' in op:
                    yield Query (self, op ['opid'], op ['ns'], op ['query'], op ['secs_running'])
                else:
                    yield Query (self, op ['opid'], op ['ns'], op ['query'])
            else:
                yield Operation (self, op ['opid'])

    def killOperation (self, opid):
        os.system ('echo "db.killOp (' + str (opid) + ')" | mongo ' + self.__address)

    def __str__ (self):
        return self.__name

class ConsoleActivator:
    def __enter__ (self):
        self.__settings = termios.tcgetattr (sys.stdin)
        tty.setcbreak (sys.stdin.fileno())
        return Console (self)

    def __exit__ (self, *ignored):
        termios.tcsetattr (sys.stdin, termios.TCSADRAIN, self.__settings)

class ConsoleDeactivator ():
    def __init__ (self, consoleActivator):
        self.__consoleActivator = consoleActivator

    def __enter__ (self):
        self.__consoleActivator.__exit__ ()

    def __exit__ (self, *ignored):
        self.__consoleActivator.__enter__ ()

class Console:
    def __init__ (self, consoleActivator):
        self.__consoleDeactivator = ConsoleDeactivator (consoleActivator)
        self.__listPrinters = (Server.listPrinter, Query.listPrinter)

    def getButton (self):
        button = sys.stdin.read (1)
        if button in ('e', 'k', 'q'):
            return button

    def checkButton (self):
        if select.select ([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
            return self.getButton ()

    def refresh (self):
        os.system ('clear')
        for listPrinter in self.__listPrinters:
            listPrinter.printLines ()
            print ()

    def askForOperation (self):
        with self.__consoleDeactivator:
            print ()
            serverName = input ('Server: ')
            if serverName:
                opid = input ('OpId: ')
                if opid:
                    return serverName, opid

class Configuration:
    def filePath (self, default = False):
        return os.path.splitext (__file__) [0] + ('.default' if default else '') + '.conf'

    def servers (self):
        configParser = ConfigParser ()
        if configParser.read (self.filePath ()):
            servers = []
            for section in configParser.sections ():
                servers.append (Server (section, configParser.get (section, 'address')))
            return servers

    def printInstructions (self):
        print ('Please create a configuration file: ' + self.filePath ())
        try:
            with open (self.filePath (default = True)) as defaultConfigurationFile:
                print ('Like this:')
                print (defaultConfigurationFile.read ())
        except IOError: pass

if __name__ == '__main__':
    configuration = Configuration ()
    servers = configuration.servers ()
    if servers:
        button = None
        with ConsoleActivator () as console:
            while button != 'q':
                if not button:
                    Server.listPrinter.reset ([server for server in servers ])
                    Query.listPrinter.reset ([operation for server in servers for operation in server.currentOperations ()])
                    console.refresh ()
                    sleep (1)
                    button = console.checkButton ()
                if button in ('e', 'k'):
                    operationInput = console.askForOperation ()
                    if operationInput:
                        operation = Query.listPrinter.getLine (operationInput)
                        if operation:
                            if button == 'e':
                                if isinstance (operation, Query):
                                    operation.printExplain ()
                                else:
                                    print ('Only queries with namespace can be explained.')
                            elif button == 'k':
                                operation.kill ()
                        else:
                            print ('Invalid operation.')
                        button = console.getButton ()
                    else:
                        button = None
    else:
        configuration.printInstructions ()