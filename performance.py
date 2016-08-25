#!/usr/bin/env python2

import os
import csv
import tools
import json
import sys
from time import sleep
from collections import OrderedDict
from datetime import datetime
from fabric.api import run, env, execute, get, put, parallel, local, cd, hide
from fabric.network import disconnect_all
from math import sqrt
from argparse import ArgumentParser
from itertools import chain
from traceback import format_exc

env.user = "root"
env.keepalive = 30

def GetRouteEth(Dst):
    def getField(text, fieldName):
        takeIt = False
        for field in text.split():
            if takeIt:
                    return field
            if field == fieldName:
                    takeIt = True
                    continue
        return ""

    with hide('running','status', 'stdout'):
        res = dict()
        res["dev"] = getField(run("ip route get %s" % Dst),"dev")
        if not res["dev"]:
            return None
        res["ip"],res["prefix"] = getField(run("ip -o a s dev %s" % res["dev"]),"inet").split("/")
    return res 

class Test(object):
    def __init__(self, confFile, description):
        self.confFile = confFile
        self.perfSettings = json.load(open(confFile,'r'))
        self.description = description
        self.scenarioConf = {}
        self.TestDateTime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.loadTime = 0
        self.rampUpTime = 0
        self.report = OrderedDict()
        self.loadTools = {}
        self.statTools = {}
        self.hostsStore = {}
        
    def createLocalDirs(self):
        # Rmove old dirs
        local("find %s -maxdepth 1 -mindepth 1 -type d -mtime +7 -exec rm -rf {} \;" % self.scenarioConf["Misc"]["ResultsDir"])
        self.nginxDir = os.path.join(self.ResultsDir, "nginx-conf")
        os.makedirs(self.nginxDir)

    @parallel
    def RunPerformanceParallel(self):
        for load in self.scenarioConf["LoadActions"].keys():
            self.prepareTools(load)
        for tool in chain.from_iterable(self.loadTools.values()):
            tool.start()
        
    @parallel
    def stopPerformanceParalel(self):
        for tool in chain.from_iterable(self.loadTools.values()):
            tool.stop()

    def RunPerformance(self):
        for load in self.scenarioConf["LoadActions"].keys():
            loadConf = self.scenarioConf["LoadActions"][load]
            self.prepareTools(load)
            for tool in self.loadTools[load] + self.statTools[load]:
                host = tool.getConfig()["Host"]
                execute(tool.start, host=host)
            sleep(loadConf["LoadTime_sec"] + loadConf["RampUpTime_sec"])
            for tool in self.loadTools[load] + self.statTools[load]:
                host = tool.getConfig()["Host"]
                execute(tool.stop, host=host)
            execute(self.killAllTmux, hosts=self.allUnits)
            
    @parallel
    def updateEtcHosts(self):
        for entry in self.scenarioConf["Servers"]["Hosts"]:
            hostname = entry.split(" ")[1]
            with hide('running','status', 'stdout'):
                self.hostsStore[env.host] = run("grep '%s$' /etc/hosts ||:" % hostname).strip()
                if self.hostsStore[env.host]:
                    run("sed -i 's/.*%s$/%s/' /etc/hosts" % (hostname,entry))
                else:
                    run("echo '%s' >> /etc/hosts" % entry)
    
    @parallel
    def restoreEtcHosts(self):
        with hide('running','status', 'stdout'):
            if self.hostsStore[env.host]:
                run("sed -i 's/.*%s$/%s/' /etc/hosts ||:" % (self.hostsStore[env.host].split(" ")[1],self.hostsStore[env.host]))
            else:
                for entry in self.scenarioConf["Servers"]["Hosts"]:
                    hostname = entry.split(" ")[1]
                    run("sed -i '/.*%s$/d' /etc/hosts" % hostname)
                

    def defineScenarioConf(self,scenario):
        self.scenarioConf["Scenario"] = scenario
        self.scenarioConf["Clients"] = self.perfSettings["ClientProfiles"][scenario["Clients"]]
        self.scenarioConf["Servers"] = self.perfSettings["ServerProfiles"][scenario["Servers"]]
        self.allClients = self.scenarioConf["Clients"]["IPs"]
        self.allServers = self.scenarioConf["Servers"]["IPs"]
        self.scenarioConf["LoadActions"] = OrderedDict()
        for load in scenario["LoadActions"]:
            self.scenarioConf["LoadActions"][load] = self.perfSettings["LoadActionProfiles"][load]
        self.scenarioConf["Misc"] = self.perfSettings["MiscProfiles"][scenario["Misc"]]
        self.allUnits = self.allClients + self.allServers
        if scenario["Type"] == "parallel":
            self.rampUpTime = max([load["RampUpTime_sec"] for (k,load) in self.scenarioConf["LoadActions"].items()])
            self.loadTime = max([load["LoadTime_sec"] for (k,load) in self.scenarioConf["LoadActions"].items()])
    
    def addScenarioToReport(self):
        scenario = self.scenarioConf["Scenario"]["Name"]
        self.report[scenario] =  OrderedDict()
        self.report[scenario]["Servers"] = ",".join(self.scenarioConf["Servers"]["IPs"])
        self.report[scenario]["Clients"] = ",".join(self.scenarioConf["Clients"]["IPs"])
        
    def nginxSetLimitRate(self):
        with hide('running','status', 'stdout'):
            rate = self.scenarioConf["Servers"]["LimitRateBytes"]
            get("/etc/nginx/conf.d/default.conf", "%s/default.conf.%s" % (self.nginxDir,env.host))
            local("cp -a %s/default.conf.%s /tmp/default.conf" % (self.nginxDir,env.host), capture=True)
            local('/usr/bin/sed -ie "s#.*limit_rate.*#    limit_rate %s;#" /tmp/default.conf' % rate)
            put("/tmp/default.conf", "/etc/nginx/conf.d/default.conf")
            run("service nginx restart >/dev/null")
    
    def configCurlLoader(self, load, profile):
        toolsList = []
        loadConf = self.scenarioConf["LoadActions"][load]
        curlConf = self.perfSettings["CurlLoaderProfiles"][profile].copy()
        if self.scenarioConf["Scenario"]["Type"] == "parallel":
            loadNodes = len(self.scenarioConf["Clients"]["IPs"])
        else:
            loadNodes = 1
        srvIP = self.scenarioConf["Servers"]["Hosts"][0].split()[0]
        curlConf["Profile"] = load
        curlConf["Host"] = env.host
        trafficNet = GetRouteEth(srvIP) 
        Ip = trafficNet["ip"]
        Net = Ip[:Ip.rindex('.')]
        curlConf["RunTime"] = loadConf["LoadTime_sec"] + loadConf["RampUpTime_sec"]
        curlConf["SimUsers"] = curlConf["SimUsers"]/(loadNodes * curlConf["CurlInstances"])
        numIPs = curlConf["IpAddrMax"] - curlConf["IpAddrMin"] + 1
        if not curlConf.has_key("IpSharedNum"):
            curlConf["IpSharedNum"] = 1
        curlConf["IpSharedNum"] = min(curlConf["IpSharedNum"],numIPs)
        curlConf["IpSharedNum"] = curlConf["IpSharedNum"] / curlConf["CurlInstances"]
        curlConf["clientsRampUpInc"] = max(1, int(curlConf["SimUsers"] / loadConf["RampUpTime_sec"]))
        curlConf["TrafficIface"] = trafficNet["dev"]
        curlConf["TrafficNet"] = trafficNet["prefix"]
        if curlConf["IpSharedNum"] == 1:
            ipPerInstance = numIPs / curlConf["CurlInstances"]
        else:
            ipPerInstance = curlConf["IpSharedNum"]
        for instance in range(curlConf["CurlInstances"]):
            instanceConf = curlConf.copy()
            if instanceConf["CurlInstances"] > 1:
                instanceConf["batchName"] = "%s%d" % (load,instance)
        
            else:
                instanceConf["batchName"] = load
            instanceConf["IpAddrMin"] = "%s.%s" % (Net, (curlConf["IpAddrMin"] + instance * ipPerInstance))
            instanceConf["IpAddrMax"] = "%s.%s" % (Net, (curlConf["IpAddrMin"] + ipPerInstance + instance * ipPerInstance - 1))
            tool = tools.CurlLoader()
            res = tool.setConfig(instanceConf)
            if res:
                toolsList.append(tool)
            else:
                print("ERROR: Failed to configure curl-loader tool.\n Configuration was:")
                print("\n".join(["%s = %s" % (k,v) for (k,v) in instanceConf.items()]))
        return toolsList
           
    def configPing(self, load, profile):
        def isIPv4(ip):
            try:
                octets = ip.strip().split(".")
                if len(octets) != 4:
                    return False
                for o in octets:
                    i = int(o)
                    if i > 255 or i < 0:
                        return False
                return True
            except:
                return False
        loadConf = self.scenarioConf["LoadActions"][load]
        pingConf = self.perfSettings["PingProfiles"][profile].copy()
        pingConf["Profile"] = load
        pingConf["Host"] = env.host
        pingConf["Interval"] = pingConf.pop("Interval_sec")
        pingConf["Size"] = pingConf.pop("PacketSize")
        pingConf["DelayStart"] = loadConf["RampUpTime_sec"]
        if pingConf["Destination"] == 'DGW':
            with hide('running','status', 'stdout'):
                pingConf["Destination"] = run("ip r s | grep default | cut -d' ' -f 3")
        if isIPv4(pingConf["Destination"]):
            tool = tools.Ping()
        else:
            print("ERROR: Unable to determine ping destination from %s" % pingConf["Destination"])
            return False
        res = tool.setConfig(pingConf)
        if res:
            return tool
        else:
            print("ERROR: Failed to configure ping tool.\n Configuration was:")
            print("\n".join(["%s = %s" % (k,v) for (k,v) in pingConf.items()]))

    def configDstat(self, load, profile):
        loadConf = self.scenarioConf["LoadActions"][load]
        dstatConf = self.perfSettings["DstatProfiles"][profile].copy()
        dstatConf["Profile"] = load
        dstatConf["Host"] = env.host
        dstatConf["Interval"] = dstatConf.pop("Interval_sec")
        dstatConf["DelayStart"] = loadConf["RampUpTime_sec"]
        tool = tools.Dstat()
        res = tool.setConfig(dstatConf)
        if res:
            return tool
        else:
            print("ERROR: Failed to configure dstat tool.\n Configuration was:")
            print("\n".join(["%s = %s" % (k,v) for (k,v) in dstatConf.items()]))

    def killAllTmux(self):
        with hide('running','status', 'stdout'):
            run("pgrep -l tmux && tmux kill-server || :")        
    
    def analyzeResults(self):

        def buildReportPairs(raw):
            res = OrderedDict()
            lines = raw.splitlines()
            keys = lines[0].rstrip(',').split(',')
            values = lines[1].rstrip(',').split(',')
            if len(keys) != len(values):
                print "ReportPairs parsing failure: keys amount is not equal to values amount. Aborting."
                raise
            for k,v in zip(keys,values):
                res[k]=float(v)
            return res

        
        def addCurlResults(first,second):
            res = OrderedDict()
            for k in first.keys():
                if k.startswith("delay"):
                    if k.endswith("min"):
                        res[k] = min(first[k],second[k])
                    elif k.endswith("max"):
                        res[k] = max(first[k],second[k])
                    elif k.endswith("avg"):
                        res[k] = (first[k]*first["num"] + second[k]*second["num"])/(first["num"] + second["num"])
                    elif k.endswith("std"):
                        res[k] = sqrt( ((first[k]**2)*first["num"] + (second[k]**2)*second["num"])/(first["num"] + second["num"]) )
                    else:
                        print "Unexpected 'delay' parameter."
                        raise
                elif k.startswith("duration"):
                    res[k] = max(first[k],second[k])
                else:
                    res[k] = first[k] + second[k]
            return res
        
        aggregate = False
        if len(self.allClients)>1 and self.scenarioConf["Scenario"]["Type"] == "parallel":
            aggregate = True
        scenario = self.scenarioConf["Scenario"]["Name"]
        if scenario not in self.report: self.report[scenario] = {}

        for tool in chain.from_iterable(self.loadTools.values() + self.statTools.values()):
            toolName = tool.getToolName()
            toolConf = tool.getConfig()
            load = toolConf["Profile"]
            if load not in self.report[scenario]: self.report[scenario][load] = {}
            if toolName not in self.report[scenario][load]: self.report[scenario][load][toolName] = {}
            try:
                res = execute(tool.getToolResults, hosts=toolConf["Host"])
            except Exception as e:
                print("Failed to get results of tool %s.\nError: %s" % (toolName,e))
            for host in res.keys():
                if toolName == "curl-loader":
                    if self.report[scenario][load][toolName].has_key(host):
                        self.report[scenario][load][toolName][host] = addCurlResults(self.report[scenario][load][toolName][host], res[host])
                    else:
                        self.report[scenario][load][toolName][host] = res[host]
                    if aggregate:
                        if self.report[scenario][load][toolName].has_key("total"):
                            self.report[scenario][load][toolName]["total"] = addCurlResults(self.report[scenario][load][toolName]["total"],res[host])
                        else:
                            self.report[scenario][load][toolName]["total"] = res[host]
                else:
                    if self.report[scenario][load][toolName].has_key(host):
                        print("WARNING: Tool %s already has results from %s !!!" % (toolName,host))
                    self.report[scenario][load][toolName][host] = res[host]
        # Removing tools
        self.loadTools = {}
        self.statTools = {}

    def generateOutputFile(self):
        def writeCSVsection(writer,section,data):
            if not data.has_key(section):
                print("Was unable to write section %s in CSV file." % section)
                return
            row = ["param/unit"]
            hosts = [host for host in data[section]]
            # Same tool can have different prameters quantity. Will use maximal set for iterations
            itemnum = [len(data[section][host].keys()) for host in hosts]
            index = itemnum.index(max(itemnum))
            row.extend(hosts)
            writer.writerow(row)
            for item in data[section][hosts[index]].keys():
                row = [item]
                values = []
                for host in hosts:
                    #Insert empty value if host doesn't have parameter item
                    if data[section][host].has_key(item):
                        values.append(data[section][host][item])
                    else:
                        values.append("")
                row.extend(values)
                writer.writerow(row)
            
        f = open(self.resultFileFullPath,'wb')
        #print self.report
        w = csv.writer(f)
        w.writerow(["Report date/time:", self.TestDateTime])
        #for scenario in self.report:
        scenario = self.scenarioConf["Scenario"]["Name"]
        sdata = self.report[scenario]
        w.writerow(["Scenario:", scenario])
        w.writerow(["Description:", self.description])
        w.writerow(["Servers:", sdata["Servers"]])
        w.writerow(["Clients:", sdata["Clients"]])
        #Writing load stats sections
        w.writerow(["Statistics section"])
        for load in self.scenarioConf["LoadActions"].keys():
            ldata = sdata[load]
            w.writerows([[],["Load Profile:",load],["Curl-Loader:"]])
            writeCSVsection(w,"curl-loader",ldata)
            w.writerow(["Dstat:"])
            writeCSVsection(w,"dstat",ldata)
            w.writerow(["Ping:"])
            writeCSVsection(w,"ping",ldata)
            
        f.close()
        print "Result file was created at %s" % self.resultFileFullPath
    
    def prepareTools(self,load):
        def getTools(toolName, toolDict):
            res = []
            for host,tool in toolDict.items():
                if tool:
                    try:
                        res.extend(tool)
                    except TypeError:
                        res.append(tool)
                else:
                    print("ERROR: Failed to initialize %s on %s" % (toolName,host))
                    exit()
            return res
        loadConf = self.scenarioConf["LoadActions"][load]
        if not len(loadConf["LoadTools"]):
            print("ERROR: No load tools were found in LoadAction profile %s" % profile)
            exit()
        if not len(loadConf["StatTools"]):
            print("ERROR: No statistic tools were found in LoadAction profile %s" % profile)
            exit()
        self.loadTools[load] = []
        self.statTools[load] = []
        for (toolName,profiles) in loadConf["LoadTools"].items():
            if toolName == "curl-loader":
                for profile in profiles:
                    res = execute(self.configCurlLoader, load, profile, hosts=self.allClients)
                    self.loadTools[load].extend(getTools(toolName,res))
            else:
                print("WARNING: loadtool %s is not supported! Skipped." % tool)
        for (toolName,profile) in loadConf["StatTools"].items():
            if toolName == "ping":
                res = execute(self.configPing, load, profile, hosts=self.allClients)
                self.statTools[load].extend(getTools(toolName, res))
            elif toolName == "dstat":
                res = execute(self.configDstat, load, profile, hosts=self.allUnits)
                self.statTools[load].extend(getTools(toolName, res))
            else: 
                print("WARNING: stattool %s is not supported! Skipped." % tool)
        
    def configureClients(self):
        self.updateEtcHosts()
        self.resultFileFullPath = os.path.join(self.ResultsDir, self.scenarioConf["Scenario"]["ResultsFileName"])
        
    def configureServers(self):
        self.nginxSetLimitRate()

    def configureRun(self):
        execute(self.killAllTmux, hosts=self.allUnits)
        execute(self.configureClients, hosts=self.scenarioConf["Clients"]["IPs"])
        execute(self.configureServers, hosts=self.scenarioConf["Servers"]["IPs"])
        
    def postRun(self):
        self.analyzeResults()
        self.generateOutputFile()
        execute(self.restoreEtcHosts, hosts=self.scenarioConf["Clients"]["IPs"])

    def Run(self, scenario):
        """ Run performance test's scenario """
        # Populating configuration elements from profiles, raise exception if profile dosn't exist
        try:
            self.defineScenarioConf(scenario)
        except:
            print "Error in populating configuration elements from profiles!\nTerminating."
            raise
        self.ResultsDir = os.path.abspath(os.path.join(self.scenarioConf["Misc"]["ResultsDir"], self.TestDateTime, self.scenarioConf["Scenario"]["Name"]))
        self.createLocalDirs()
        self.configureRun()
        
        self.addScenarioToReport()
        
        if scenario["Type"]=="parallel":
            execute(self.RunPerformanceParallel, hosts=self.allClients)
            for tool in self.statTools:
                host = tool.getConfig()["Host"]
                execute(tool.start, host=host)
            sleep(self.loadTime + self.rampUpTime)
            execute(self.stopPerformanceParalel, hosts=self.allClients)
            for tool in self.statTools:
                host = tool.getConfig()["Host"]
                execute(tool.stop, host=host)
            execute(self.getJmeterStats, hosts = self.allClients)
            execute(self.killAllTmux, hosts=self.allUnits)
        else:
            self.RunPerformance()
        
        self.postRun()
        print("\nPerformance log for %s:\n\n%s\n\n" % (self.scenarioConf["Scenario"]["Name"], self.TestDateTime))
    
 
        
if __name__ == "__main__":
    def getOptions():
        parser = ArgumentParser()
        parser.add_argument("-f","--file", dest="confFile", default="config.json", help="Path to configuration json file.")
        parser.add_argument("-d","--desc", dest="description", default="", help="Test run brief description.")
        return parser.parse_args()
    #try:
    # Configuration (test.json)
    opts = getOptions()
    t = Test(opts.confFile,opts.description)
    #sys.exit(0)
    for scenario in t.perfSettings["Scenarios"]:
        #print "DBG:\n%s" % str(scenario)
        if scenario["Active"]:
            t.Run(scenario)
    #except Exception, e:
    #    msg = "%s\n%s" % (str(e),format_exc())
    #    raise e
    #close all SSH connections
    disconnect_all()

