import os
import csv
import StringIO
from shutil import rmtree
from fabric.network import disconnect_all
from fabric.api import run, cd, put, hide, settings
from subprocess import call, Popen, PIPE, STDOUT
from collections import OrderedDict
from scipy import mean, std
from time import sleep

class Tool(object):
    """ Base class for tools """

    def __init__(self, toolName, execute, debug):
        self.resultsDir = os.path.join("/tmp",toolName,str(self.__hash__()))
        self.remoteExec = False
        self.__toolName = toolName
        self.conf = {}
        if execute == 'remote':
            self.remoteExec = True
        self.debug = debug

    def _hide(self):
        if self.debug:
            res = hide('running','status','stdout')
        else:
            res = settings(hide('everything'),warn_only=True)
        return res

    def log(self, message):
        if self.debug:
            print(message)

    def getToolName(self):
        return self.__toolName

    def getResultsDir(self):
        return self.resultsDir

    def setResultsDir(self, resultsDir):
        self.resultsDir = os.path.normpath(os.path.normcase(os.path.abspath(resultsDir)))    
    
    def getResultsFile(self):
        if self.conf.has_key("resultsFile"):
            return self.conf["resultsFile"]
        else:
            print("WARNING: resultsFile is not set yet!")
            return False

    def setResultsFile(self,resultsFile):
        self.conf["resultsFile"] = os.path.join(self.resultsDir,resultsFile)

    def getToolID(self):
        return str(self.__hash__())
    
    def setConfig(self, configDict):
        if self.validateConfig(configDict):
            self.resetConfig()
            self.applyConfig(configDict)
            return True
        else:
            return False
            
    def getConfig(self):
        # Implemented by child class
        res = "WARNING: Method %s is not implemented!" % __name__
        print(res)
        return res
    
    def getResultsFromFile(self,resultsFilePath):
        FilePath = os.path.normpath(resultsFilePath)
        if os.path.exists(FilePath):
            self.setResultsDir(os.path.dirname(FilePath))
            self.setResultsFile(os.path.basename(FilePath))
            return self.getResults()
        else:
            print("ERROR: Results file %s does not exists!" % FilePath)
            return False
    
    def isRunning(self):
        if not self.conf.has_key("tmuxID"):
            return False
        if self.remoteExec:
            with self._hide():
                res = run("tmux has-session -t %s" % self.conf["tmuxID"], warn_only=True)
            return res.succeeded
        else:
            res = call("tmux has-session -t %s " % self.conf["tmuxID"], shell=True, stdout=PIPE, stderr=PIPE)
            return res == 0

    def start(self):
        if not self.conf.has_key("tmuxID"):
            print("WARNING: tmuxID is not set!")
            return
        if self.isRunning():
            print("WARNING: %s is already running with tmuxID %s\n" % (self.getToolName(),self.conf["tmuxID"]))
            return
        if self.conf.has_key("DelayStart"):
            self.conf["toolCmd"] = "sleep %(DelayStart)d && %(toolCmd)s" % self.conf
        configString = ""
        if self.conf["configFile"]:
            configString = " with config file %s" % self.conf["configFile"]
        if self.remoteExec:
            with cd(self.resultsDir):
                self.log("Starting tmux session %s for %s%s" % (self.conf["tmuxID"], self.getToolName(), configString))
                with self._hide():
                    run("tmux new-session -d -s %s \'%s\'" % (self.conf["tmuxID"], self.conf["toolCmd"]))
        else:
            self.log("Starting tmux session %s for %s%s" % (self.conf["tmuxID"], self.getToolName(), configString))
            call("cd %s; tmux new-session -d -s %s \'%s\'" % \
              (self.resultsDir, self.conf["tmuxID"], self.conf["toolCmd"]), shell=True, stdout=PIPE, stderr=PIPE)
        
    def stop(self):
        if not self.conf.has_key("tmuxID"):
            print("WARNING: tmuxID is not set!")
            return
        if not self.isRunning():
            print("WARNING: %s with tmuxID %s is not running\n" % (self.getToolName(),self.conf["tmuxID"]))
            return
        if self.remoteExec:
            self.log("Sending Ctrl-C to tmux session %s" % (self.conf["tmuxID"]))
            with self._hide():
                run("tmux send-keys -t %s 'C-c'" % self.conf["tmuxID"])
        else:
            self.log("Sending Ctrl-C to tmux session %s" % (self.conf["tmuxID"]))
            call("tmux send-keys -t %s 'C-c'" % self.conf["tmuxID"], shell=True, stdout=PIPE, stderr=PIPE)
        # ensure tmux session doesn't exist
        cycles = 50
        while self.isRunning() and cycles:
            cycles -= 1
            sleep(0.1)
    
    def createResDir(self):
        if self.remoteExec:
            with self._hide():
                run("mkdir -p %s" % self.resultsDir, warn_only=True)
        else:
            if not os.path.exists(self.resultsDir):
                os.makedirs(self.resultsDir)
    
    def removeResDir(self):
        # There are some cases when Ctrl-C in stop doesn't take effect
        # Killing tmux session directly first
        if self.conf.has_key("tmuxID"):
            if self.isRunning():
                if self.remoteExec:
                    self.log("Killing tmux session %s" % (self.conf["tmuxID"]))
                    with self._hide():
                        run("tmux kill-session -t %s" % self.conf["tmuxID"])
                else:
                    self.log("Killing tmux session %s" % (self.conf["tmuxID"]))
                    call("tmux kill-session -t %s" % self.conf["tmuxID"], shell=True, stdout=PIPE, stderr=PIPE)
        # And now removing directory
        if self.remoteExec:
            with self._hide():
                run("rm -rf %s" % self.resultsDir, warn_only=True)
        else:
            if os.path.exists(self.resultsDir):
                rmtree(self.resultsDir,ignore_errors=True)
        disconnect_all()
    
    def isMandatoryPresent(self, mandatoryList, configDict):
        for param in mandatoryList:
            if not configDict.has_key(param):
                print "ERROR: Your config has no %s param!" % param
                return False
            elif str(configDict[param]) == "":
                print "ERROR: Your config has EMPTY %s param!" % param
                return False
        return True
    
    def getToolResults(self):
        try:
            res = self.getResults()
        except AnalyzeError as e:
            print("WARNING: There is failure in getting results for tool %s" % self.getToolName())
            self.log("DEBUG:")
            self.log("\n".join(["%s = %s" % (k,v) for k,v in e.details.iteritems()]))
            res = None
        finally:
            self.removeResDir()
        return res
    
class AnalyzeError(Exception):
    def __init__(self, error="AnalyzeError"):
        Exception.__init__(self, error)
        self.details = {}
        
class CurlLoader(Tool):
    from collections import OrderedDict
    """CurlLoader should be provided with following parameters:

SimUsers                    - amount of users to be simulated by tool
clientsRampUpInc            - ramp-up simusers per second 
IpAddrMin                   - last octet of first IP address in test range on the node
IpAddrMax                   - last octet of last IP address in test range on the node
batchName                   - test name. Used in results files naming. 
Host                        - management IP of the node running curl-loader
TrafficNet                  - network address of data interface (CIDR form)
(o) TrafficIface            - data network interface name (can be obtained based on TrafficNet)
CyclesNum                   - amount of cycles to run the batch
(o) RunTime                 - amount of time to run the batch
(o) RequestRate             - use fixed HTTP requests rate (best effort)
(o) UserAgent               - HTTP's User Agent header to send to the server

Urls                    - list of URLs and it's fetching parameters:
    * UrlName           - human readable, meaningfull URL name
    * (o) ClientLimitRateBytes    - download rate limitation per simulated user in bytes per second
    * (d) RequestType       - HTTP request method (GET, POST, HEAD etc) (default=GET)
    * (d) Repetitions       - amount of URLs fetching repeatitions (default=1)
    * (d) FreshConnect      - establish new TCP connection for URL fetching (default=1)
    * (o) UrlRandomRange    - URL randomization range for token replacement
    * (o) UrlRandomToken    - URL randomization token
    * (o) FetchProbability  - Probability of URL's fetching
    * (o) FetchDecideOnce   - URL probablility fetching decision on startup only
    * (d) ConnectTimer      - DNS resolving and TCP connect time-out in seconds (default=0)
    * (d) CompletionTimer   - URL fetching timeout in milliseconds (default=0)
    * (d) SleepTimer        - sleep time between URL fetching (default=0)
    * (o) Header            - add / replace HTTP header"""
    TOOL_NAME = "curl-loader" 
    CONFIG_MAP_G = {"batchName": "BATCH_NAME",\
    "clientsRampUpInc":     "CLIENTS_RAMPUP_INC",\
    "SimUsers":             "CLIENTS_NUM_MAX",\
    "SimUsersInit":         "CLIENTS_NUM_START",\
    "TrafficIface":         "INTERFACE",\
    "TrafficNet":           "NETMASK",\
    "IpAddrMin":            "IP_ADDR_MIN",\
    "IpAddrMax":            "IP_ADDR_MAX",\
    "IpSharedNum":          "IP_SHARED_NUM",\
    "CyclesNum":            "CYCLES_NUM",\
    "RunTime":              "RUN_TIME",\
    "RequestRate":          "REQ_RATE",\
    "UserAgent":            "USER_AGENT"}
    # URL should be first - using OrderedDict
    CONFIG_MAP_U = OrderedDict([\
    ("Url", "URL"),\
    ("UrlName",              "URL_SHORT_NAME"),\
    ("ClientLimitRateBytes", "TRANSFER_LIMIT_RATE"),\
    ("RequestType",          "REQUEST_TYPE"),\
    ("FreshConnect",         "FRESH_CONNECT"),\
    ("UrlRandomRange",       "URL_RANDOM_RANGE"),\
    ("UrlRandomToken",       "URL_RANDOM_TOKEN"),\
    ("FetchProbability",     "FETCH_PROBABILITY"),\
    ("FetchDecideOnce",      "FETCH_PROBABILITY_ONCE"),\
    ("ConnectTimer",         "TIMER_TCP_CONN_SETUP"),\
    ("CompletionTimer",      "TIMER_URL_COMPLETION"),\
    ("SleepTimer",           "TIMER_AFTER_URL_SLEEP"),\
    ("Header",               "HEADER")])
    MANDATORY_G = ["batchName","SimUsers","TrafficIface",\
        "TrafficNet","IpAddrMin","IpAddrMax","Urls"]
    MANDATORY_U = ["Url","RequestType"]
    
    def __init__(self,executeMode='remote', debug=False):
        Tool.__init__(self, CurlLoader.TOOL_NAME, executeMode, debug)
        self.resetConfig()
    
    def resetConfig(self):
        """ Clear configuration and reset default parameters' values """
        self.conf = {}

    def validateConfig(self,configDict):
        """ Checks that user provided minimal configuration for load execution
        Returns True or False """
        # Check general section parameters
        if not self.isMandatoryPresent(CurlLoader.MANDATORY_G, configDict):
            return False
        # Check URL section parameters
        for url in configDict["Urls"]:
            if not self.isMandatoryPresent(CurlLoader.MANDATORY_U, url):
                return False
        return True

    def applyConfig(self,configDict):
        """ Apply provided tool configuration.
        Create configuration files in resultsDir """
        self.conf = configDict.copy()
        # Config has to have CYCLES_NUM, -1 for unlimited
        if not self.conf.has_key("CyclesNum"):
            self.conf["CyclesNum"] = "-1"
        if not self.conf.has_key("TPI"):
            self.conf["TPI"] = 1
        if self.conf["TPI"] > 1:
            self.conf["IpSharedNum"] = self.conf["TPI"]
        self.conf["tmuxID"] = "%s_%s" % (self.conf["batchName"],self.getToolID())
        # Composing configuration file content
        configLines = ["########### GENERAL SECTION ################################", ""]
        for param in CurlLoader.CONFIG_MAP_G:
            if self.conf.has_key(param):
                if str(self.conf[param]) != "":
                    configLines.append("%s = %s" % (CurlLoader.CONFIG_MAP_G[param], self.conf[param]))
        configLines.append("DUMP_OPSTATS = no")
        urlsNum = 0

        urlLines = ["", "########### URLS SECTION ####################################"]
        for url in self.conf["Urls"]:
            if not url.has_key("Repeatitions"):
                url["Repeatitions"] = 1
            for i in range(url["Repeatitions"]):
                urlsNum += 1
                for param in CurlLoader.CONFIG_MAP_U:
                    if url.has_key(param):
                        if str(url[param]) != "":
                            urlLines.append("%s = %s" % (CurlLoader.CONFIG_MAP_U[param], url[param]))
                
        configLines.append("URLS_NUM = %s" % urlsNum)
        configLines.extend(urlLines)
        self.conf["configFile"] = os.path.join(self.resultsDir, "%s.conf" % self.conf["batchName"])
        self.createResDir()
        if self.remoteExec:
            tmp = StringIO.StringIO("\n".join(configLines))
            put(tmp,self.conf["configFile"])
        else:
            with open(self.conf["configFile"], "wt") as f:
                f.write("\n".join(configLines))
        if self.conf["TPI"] > 1:
            threads = "-t%d" % self.conf["TPI"]
            self.setResultsFile("%s_0.txt" % self.conf["batchName"])
        else:
            threads = ""
            self.setResultsFile("%s.txt" % self.conf["batchName"])
        self.conf["toolCmd"] = "nice -n -20 curl-loader %s -w -f %s" % \
                  (threads, self.conf["configFile"])
    
    def getConfig(self):
        return self.conf

    def getResults(self):
        """ Parses results file, analyses and returns summary statistics """
        # Initialize variables
        raw = dict()
        _len = 0
        dataOffset = 0
        hasRampUp = True
        hasSummary = False
        preprocessCmd = "grep -v 'H/F/S' %s | grep -v '^R' ||:" % self.conf["resultsFile"]
        raw["total_attempt"] = 0
        raw["total_success"] = 0
        raw["total_httperr"] = 0
        raw["total_connerr"] = 0
        raw["total_touterr"] = 0
        raw["runtime"]       = []
        raw["clients"]       = []
        raw["attempt"]       = []
        raw["1xx"]           = []
        raw["2xx"]           = []
        raw["3xx"]           = []
        raw["success"]       = []
        raw["4xx"]           = []
        raw["5xx"]           = []
        raw["httperr"]       = []
        raw["err"]           = []
        raw["terr"]          = []
        raw["d"]             = []
        raw["d2xx"]          = []
        raw["ti"]            = []
        raw["to"]            = []
        raw["first_err"]     = 0
        # Parsing and filling raw data structure 
        if self.remoteExec:
            with self._hide():
                fContent = StringIO.StringIO(run(preprocessCmd)).readlines()
        else:
            fContent = Popen(preprocessCmd, stdout=PIPE, stderr=STDOUT, shell=True).stdout.readlines()
        if not len(fContent):
            error = AnalyzeError("ERROR: Results file is empty!")
            error.details["Tool"] = self.getToolName()
            error.details["preprocessCmd"] = preprocessCmd
            error.details["ToolConfig"] = self.getConfig()
            raise error
        for line in fContent:
            data = line.rstrip("\n").split(",")
            if data[0] == "*":
                # Summary separation line found
                hasSummary = True
                continue
            for i in range(len(data)):
                try:
                    data[i] = int(data[i])
                except:
                    continue
            if _len > 0 and not hasSummary:
                elapsed = float(data[0]) - raw["runtime"][_len - 1]
                if hasRampUp and data[2] == raw["clients"][_len - 1]:
                    dataOffset = _len - 1
                    hasRampUp = False
            else:
                elapsed = 1.0
            # curl-loader-0.56 counters:
            # RunTime(sec),Appl,Clients,Req,1xx,2xx,3xx,4xx,5xx,Err,T-Err,D,D-2xx,Ti,To

            #/index/ - /explanation/
            # 0  - run-time in seconds;
            # 3  - requests num;
            # 4  - 1xx success num;
            # 5  - 2xx success num;
            # 6  - 3xx redirects num;
            # 7  - client 4xx errors num;
            # 8  - server 5xx errors num;
            # 9  - other errors num, like resolving, tcp-connect, server closing or empty
            #      responses number (Err);
            # 10 - url completion time expiration errors (T-Err);
            # 11 - average application server Delay (msec), estimated as the time between HTTP
            #      request and HTTP response without taking into the account network latency (RTT)
            #      (D);
            # 12 - average application server Delay for 2xx (success) HTTP-responses, as above,
            #      but only for 2xx responses. The motivation for that is that 3xx redirections and
            #      5xx server errors/rejects may not necessarily provide a true indication of a
            #      testing server working functionality (D-2xx);
            # 13 - throughput in, batch average, Bytes/sec (T-In);
            # 14 - throughput out, batch average, Bytes/sec (T-Out);
            raw["runtime"].append(data[0])
            raw["clients"].append(data[2])
            # Normalize cumulative data
            raw["attempt"].append(data[3]/elapsed)
            raw["1xx"].append(data[4]/elapsed)
            raw["2xx"].append(data[5]/elapsed)
            raw["3xx"].append(data[6]/elapsed)
            raw["success"].append((data[4]+data[5]+data[6])/elapsed)
            raw["4xx"].append(data[7]/elapsed)
            raw["5xx"].append(data[8]/elapsed)
            raw["httperr"].append((data[7]+data[8])/elapsed)
            raw["err"].append(data[9]/elapsed)
            raw["terr"].append(data[10]/elapsed)
            raw["d"].append(data[11])
            raw["d2xx"].append(data[12])
            # Convert bytes per sec to Mbits per sec
            raw["ti"].append(data[13]*8.0/1000000)
            raw["to"].append(data[14]*8.0/1000000)
            if not hasSummary:
                _len += 1
                # Totals
                raw["total_attempt"] += data[3]
                raw["total_success"] += data[4] + data[5] + data[6]
                raw["total_httperr"] += data[7] + data[8]
                raw["total_connerr"] += data[9]
                raw["total_touterr"] += data[10]
                if not raw["first_err"] and raw["total_connerr"]:
                    raw["first_err"] = _len - 1
            else:
                # Not advancing _len since last values are summary
                # Taking totals from summary
                raw["total_attempt"] = data[3]
                raw["total_success"] = data[4] + data[5] + data[6]
                raw["total_httperr"] = data[7] + data[8]
                raw["total_connerr"] = data[9]
                raw["total_touterr"] = data[10]
        # Analysing and summarising raw data
        res = OrderedDict()
        last = _len-1
        if hasSummary:
            last -= 1
        res["duration_sec"] = (raw["runtime"][-1] - raw["runtime"][dataOffset])
        res["total_attempt"] = raw["total_attempt"]
        res["total_success"] = raw["total_success"]
        res["total_httperr"] = raw["total_httperr"]
        res["total_connerr"] = raw["total_connerr"]
        res["total_touterr"] = raw["total_touterr"]
        res["clients_min"] = min(raw["clients"][dataOffset:last]) 
        res["clients_max"] = max(raw["clients"][dataOffset:last]) 
        res["clients_avg"] = mean(raw["clients"][dataOffset:last]) 
        res["clients_std"] = std(raw["clients"][dataOffset:last]) 
        res["tps_attempt_min"] = min(raw["attempt"][dataOffset:last]) 
        res["tps_attempt_max"] = max(raw["attempt"][dataOffset:last]) 
        res["tps_attempt_avg"] = mean(raw["attempt"][dataOffset:last]) 
        res["tps_attempt_std"] = std(raw["attempt"][dataOffset:last]) 
        res["tps_success_min"] = min(raw["success"][dataOffset:last]) 
        res["tps_success_max"] = max(raw["success"][dataOffset:last]) 
        res["tps_success_avg"] = mean(raw["success"][dataOffset:last]) 
        res["tps_success_std"] = std(raw["success"][dataOffset:last]) 
        res["delay_ms_min"] = min(raw["d2xx"][dataOffset:last]) 
        res["delay_ms_max"] = max(raw["d2xx"][dataOffset:last]) 
        res["delay_ms_avg"] = mean(raw["d2xx"][dataOffset:last]) 
        res["delay_ms_std"] = std(raw["d2xx"][dataOffset:last]) 
        res["throughput_in_mbps_min"] = min(raw["ti"][dataOffset:last]) 
        res["throughput_in_mbps_max"] = max(raw["ti"][dataOffset:last]) 
        res["throughput_in_mbps_avg"] = mean(raw["ti"][dataOffset:last]) 
        res["throughput_in_mbps_std"] = std(raw["ti"][dataOffset:last]) 
        res["throughtput_out_mbps_min"] = min(raw["to"][dataOffset:last]) 
        res["throughtput_out_mbps_max"] = max(raw["to"][dataOffset:last]) 
        res["throughtput_out_mbps_avg"] = mean(raw["to"][dataOffset:last]) 
        res["throughtput_out_mbps_std"] = std(raw["to"][dataOffset:last])
        if raw["first_err"]:
            res["tps_attempt_first_err"] = raw["attempt"][raw["first_err"]]
            res["tps_success_first_err"] = raw["success"][raw["first_err"]]
            res["clients_first_err"] = raw["clients"][raw["first_err"]]
            res["delay_first_err_ms_max"] = raw["d2xx"][raw["first_err"]]
            res["throughput_in_mbps_first_err"] = raw["ti"][raw["first_err"]]
            res["throughput_out_mbps_first_err"] = raw["to"][raw["first_err"]]
        else:
            res["tps_attempt_first_err"] = 0 
            res["tps_success_first_err"] = 0
            res["clients_first_err"] = 0
            res["delay_first_err_ms_max"] = 0
            res["throughput_in_mbps_first_err"] = 0
            res["throughput_out_mbps_first_err"] = 0
        if hasSummary:
            res["throughput_in_mbps_avg"] = raw["ti"][-1] 
            res["throughtput_out_mbps_avg"] = raw["to"][-1]
            res["delay_ms_avg"] = raw["d2xx"][-1]
        res["summary"] = int(hasSummary)
            
        res["num"] = _len - dataOffset
        return res

class Ping(Tool):
    """ Ping tool """
    MANDATORY = ["Interval", "Destination", "Size"]
    TOOL_NAME = "ping"

    def __init__(self, executeMode='remote', debug=False):
        Tool.__init__(self, Ping.TOOL_NAME , executeMode, debug)
        self.resetConfig()
        
    def resetConfig(self):
        """ Clear configuration and reset default parameters' values """
        self.conf = {}
    
    def validateConfig(self,configDict):
        """ Checks that user provided minimal workable configuration
        Returns True or False """
        if not self.isMandatoryPresent(Ping.MANDATORY, configDict):
            return False
        return True
    
    def applyConfig(self,configDict):
        """ Apply provided tool configuration.
        Create configuration files in resultsDir """
        self.conf = configDict.copy()
        self.conf["tmuxID"] = "%s_%s" % (self.getToolName(),self.getToolID())
        self.setResultsFile("%s.txt" % self.getToolName())
        self.conf["toolCmd"] = "ping -n -q -s %(Size)s -i %(Interval)s %(Destination)s &>%(resultsFile)s" % self.conf
        self.conf["configFile"] = None
        self.createResDir()
    
    def getConfig(self):
        return self.conf

    def getResults(self):
        res = OrderedDict()
        try:
            if self.remoteExec:
                with self._hide():
                    ping_stat = run("tail -2 %(resultsFile)s" % self.conf).splitlines()
            else:
                ping_stat = Popen("tail -2 %(resultsFile)s" % self.conf,stdout=PIPE,stderr=STDOUT,shell=True).stdout.read().splitlines()
        except:
            error = AnalyzeError("ERROR: failed to get results content")
            error.details["Tool"] = self.getToolName()
            error.details["preprocessCmd"] = "tail -2 %(resultsFile)s" % self.conf
            error.details["ToolConfig"] = self.getConfig()
            raise error
        # Ping statistics output:
        #4 packets transmitted, 4 received, 0% packet loss, time 10414ms
        #rtt min/avg/max/mdev = 0.256/0.542/1.308/0.442 ms
        # - - - - - - 
        #448 packets transmitted, 420 received, +3 errors, 6% packet loss, time 447404ms
        #rtt min/avg/max/mdev = 0.155/7.415/2001.444/108.963 ms, pipe 3
        packets = ping_stat[0].split(",")
        res["sent"] = int(packets[0].split()[0])
        res["received"] = int(packets[1].split()[0])
        if not res["received"]:
            #No ping echo reply was received
            res["loss_%"] = 0
            res["rtt_ms_min"] = 0
            res["rtt_ms_avg"] = 0
            res["rtt_ms_max"] = 0
            res["rtt_ms_mdev"] = 0
        else:
            stats = ping_stat[1].split("/")
            delta = 0
            if packets[2][0] == "+":
                res["loss_%"] = int(packets[3].split()[0].split("%")[0])
            else:
                res["loss_%"] = int(packets[2].split()[0].split("%")[0])
            res["rtt_ms_min"] = float(stats[3].split()[-1]) 
            res["rtt_ms_avg"] = float(stats[4])
            res["rtt_ms_max"] = float(stats[5])
            res["rtt_ms_mdev"] = float(stats[6].split()[0])
        return res

class Dstat(Tool):
    """ Dstat tool """
    MANDATORY = ["Interval"]
    TOOL_NAME = "dstat"

    def __init__(self, executeMode='remote', debug=False):
        Tool.__init__(self, Dstat.TOOL_NAME , executeMode, debug)
        self.resetConfig()
        
    def resetConfig(self):
        """ Clear configuration and reset default parameters' values """
        self.conf = {}
    
    def validateConfig(self,configDict):
        """ Checks that user provided minimal workable configuration
        Returns True or False """
        if not self.isMandatoryPresent(Dstat.MANDATORY, configDict):
            return False
        return True
    
    def applyConfig(self,configDict):
        """ Apply provided tool configuration.
        Create configuration files in resultsDir """
        self.conf = configDict.copy()
        if self.conf.has_key("TrafficIface"):
            self.conf["TrafficIface"] = "-n -N %(TrafficIface)s" % self.conf
        else:
            self.conf["TrafficIface"] = ""
        self.conf["tmuxID"] = "%s_%s" % (self.getToolName(),self.getToolID())
        self.setResultsFile("%s.csv" % self.getToolName())
        self.conf["toolCmd"] = "dstat -T -c -C total -d -m %(TrafficIface)s --output %(resultsFile)s %(Interval)s" % self.conf
        self.conf["configFile"] = None
        self.createResDir()
    
    def getConfig(self):
        return self.conf
    
    def getResults(self):
        raw = OrderedDict()
        try:
            if self.remoteExec:
                with self._hide():
                    csvdata = StringIO.StringIO(run("cat %(resultsFile)s" % self.conf))
            else:
                csvdata = open("%(resultsFile)s" % self.conf,'r')
        except:
            error = AnalyzeError("ERROR: failed to get results content")
            error.details["Tool"] = self.getToolName()
            error.details["ToolConfig"] = self.getConfig()
            raise error
        # Read 5 lines of meta header
        for i in range(5):
            csvdata.readline()
        # Read header and subheader
        header = csvdata.readline().strip().split(",")
        subheader = csvdata.readline().strip().split(",")
        fieldNames = []
        curheader = ""
        # Construct final fields names
        for (h,s) in zip(header,subheader):
            if h:
                curheader = h.strip("\"").replace(" ","_")
            fieldNames.append("%s_%s" % (curheader,s.strip("\"")))
            raw[fieldNames[-1]] = []
        # Reading data into raw
        fContent = csv.DictReader(csvdata,fieldNames)
        for row in fContent:
            for field in fieldNames:
                raw[field].append(float(row[field]))
        csvdata.close()
        # Calculating summaries
        res = OrderedDict()
        for field in fieldNames:
            # Skip timestamp column
            if field == "epoch_epoch":
                continue
            res["%s_min" % field] = min(raw[field])
            res["%s_max" % field] = max(raw[field])
            res["%s_avg" % field] = mean(raw[field])
            res["%s_std" % field] = std(raw[field])
        # Add amount of observation points
        res["num"] = len(raw[fieldNames[0]])
        return res

class Ifstat(Tool):
    """ Network Interfaces statistics tool """
    MANDATORY = []
    TOOL_NAME = "ifstat"

    def __init__(self, executeMode='remote', debug=False):
        Tool.__init__(self, Ifstat.TOOL_NAME , executeMode, debug)
        self.resetConfig()
        
    def resetConfig(self):
        """ Clear configuration and reset default parameters' values """
        self.conf = {}
    
    def validateConfig(self,configDict):
        """ Checks that user provided minimal workable configuration
        Returns True or False """
        if not self.isMandatoryPresent(Ifstat.MANDATORY, configDict):
            return False
        return True
    
    def applyConfig(self,configDict):
        """ Apply provided tool configuration.
        Create configuration files in resultsDir """
        self.conf = configDict.copy()
        self.conf["tmuxID"] = "%s_%s" % (self.getToolName(),self.getToolID())
        self.setResultsFile("%s.txt" % self.getToolName())
        self.conf["toolCmd"] = "cat /proc/net/dev >%(resultsFile)s; trap \"cat /proc/net/dev >>%(resultsFile)s; exit\" SIGINT; while /bin/true; do sleep 5;done" % self.conf
        self.conf["configFile"] = None
        self.createResDir()
    
    def getConfig(self):
        return self.conf
    
    def getResults(self):
        res = OrderedDict()
        try:
            if self.remoteExec:
                with self._hide():
                    fdata = StringIO.StringIO(run("cat %(resultsFile)s" % self.conf)).readlines()
            else:
                fdata = open("%(resultsFile)s" % self.conf,'r').readlines()
        except:
            error = AnalyzeError("ERROR: failed to get results content")
            error.details["Tool"] = self.getToolName()
            error.details["ToolConfig"] = self.getConfig()
            raise error

        params = []
        res = {}
        groups = fdata[1].strip().split("|")
        # First group is Receive, second - Transmit
        for param in groups[1].split():
            params.append("rx-%s" % param)
        for param in groups[2].split():
            params.append("tx-%s" % param)
        for line in fdata[2:]:
            # Getting iface name
            iface,data = [a.strip() for a in line.strip().split(":")]
            idx=0
            if res.has_key(iface):
                for v in data.split():
                    res[iface][params[idx]] = int(v) - res[iface][params[idx]]
                    idx += 1
            else:
                res[iface] = {}
                for v in data.split():
                    res[iface][params[idx]] = int(v)
                    idx += 1
        return res

