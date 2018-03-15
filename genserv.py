#!/usr/bin/env python
#------------------------------------------------------------
#    FILE: genserv.py
# PURPOSE: Flask app for generator monitor web app
#
#  AUTHOR: Jason G Yates
#    DATE: 20-Dec-2016
#
# MODIFICATIONS:
#------------------------------------------------------------

from flask import Flask, render_template, request, jsonify, session
import sys, signal, os, socket, atexit, time, subprocess
import mylog, myclient, mythread
import urlparse
import re

try:
    from ConfigParser import RawConfigParser
except ImportError as e:
    from configparser import RawConfigParser

#------------------------------------------------------------
app = Flask(__name__,static_url_path='')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300

HTTPAuthUser = None
HTTPAuthPass = None
bUseSecureHTTP = False
bUseSelfSignedCert = True
SSLContext = None
HTTPPort = 8000
loglocation = "/var/log/"
clientport = 0
log = None
AppPath = ""

#------------------------------------------------------------
@app.after_request
def add_header(r):
    """
    Force cache header
    """
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"

    return r
#------------------------------------------------------------
@app.route('/', methods=['GET'])
def root():

    if HTTPAuthUser != None and HTTPAuthPass != None:
        if not session.get('logged_in'):
            return render_template('login.html')
        else:
            return app.send_static_file('index.html')
    else:
        return app.send_static_file('index.html')

#------------------------------------------------------------
@app.route('/internal', methods=['GET'])
def display_internal():

    if HTTPAuthUser != None and HTTPAuthPass != None:
        if not session.get('logged_in'):
            return render_template('login.html')
        else:
            return app.send_static_file('internal.html')
    else:
        return app.send_static_file('internal.html')

#------------------------------------------------------------
@app.route('/', methods=['POST'])
def do_admin_login():

    if request.form['password'] == HTTPAuthPass and request.form['username'] == HTTPAuthUser:
        session['logged_in'] = True
        return root()
    else:
        return render_template('login.html')

#------------------------------------------------------------
@app.route("/cmd/<command>")
def command(command):

    if HTTPAuthUser == None or HTTPAuthPass == None:
        return ProcessCommand(command)

    if not session.get('logged_in'):
               return render_template('login.html')
    else:
        return ProcessCommand(command)

#------------------------------------------------------------
def ProcessCommand(command):

    if command in ["status", "status_json", "outage", "outage_json", "maint", "maint_json", "logs", "logs_json", "monitor", "monitor_json", "registers_json", "allregs_json", "getbase", "getsitename", "setexercise", "setquiet", "getexercise","setremote", "settime", "reload"]:
        finalcommand = "generator: " + command
        try:
            if command == "setexercise":
                settimestr = request.args.get('setexercise', 0, type=str)
                finalcommand += "=" + settimestr
            if command == "setquiet":
                setquietstr = request.args.get('setquiet', 0, type=str)
                finalcommand += "=" + setquietstr
            if command == "setremote":
                setremotestr = request.args.get('setremote', 0, type=str)
                finalcommand += "=" + setremotestr

            data = MyClientInterface.ProcessMonitorCommand(finalcommand)

        except Exception as e1:
            data = "Retry"
            log.error("Error on command function" + str(e1))

        if command == "reload":
                Reload()                # reload Flask App

        if command in ["status_json", "outage_json", "maint_json", "monitor_json", "logs_json", "registers_json", "allregs_json"]:
            return data
        return jsonify(data)

    elif command in ["update"]:
        Update()
        return "OK"

    elif command in ["notifications"]:
        data = GetNotifications()
        return jsonify(data)

    elif command in ["settings"]:
        data = GetSettings()
        return jsonify(data)

    elif command in ["setnotifications"]:
        SaveNotifications(request.args.get('setnotifications', 0, type=str));
        return "OK"

    elif command in ["setsettings"]:
        # SaveSettings((request.args.get('setsettings', 0, type=str)));
        SaveSettings(request.args.get('setsettings', 0, type=str));
        return "OK"

    else:
        return render_template('command_template.html', command = command)

#------------------------------------------------------------
def GetNotifications():

    ### array containing information on the parameters
    ## 1st: email address
    ## 2nd: sort order, aka row number
    ## 3rd: comma delimited list of notidications that are enabled

    allEmails = []
    allNotifications = {}

    try:
        # Read contents from file as a single string
        file_handle = open("/etc/mymail.conf", 'r')
        file_string = file_handle.read()
        file_handle.close()

        for line in file_string.splitlines():
           if not line.isspace():
              parts = findConfigLine(line)
              if (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] == "email_recipient")):
                 allEmails = parts[4].split(',')
                 i = 0
                 while i < len(allEmails):
                    allNotifications[allEmails[i]] = [i+1]
                    i += 1
              elif ((len(allEmails) > 0) and parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] in allEmails)):
                 allNotifications[parts[2]].append(parts[4])

    except Exception as e1:
        log.error("Error Reading Config File: " + str(e1))

    return allNotifications

#------------------------------------------------------------
def SaveNotifications(query_string):
    notifications = dict(urlparse.parse_qs(query_string, 1))
    oldEmails = []

    notifications_order_string = ",".join([v[0] for v in urlparse.parse_qsl(query_string, 1)])

    try:
        # Read contents from file as a single string
        file_handle = open("/etc/mymail.conf", 'r')
        file_string = file_handle.read()
        file_handle.close()

        for line in file_string.splitlines():
           if not line.isspace():
              parts = findConfigLine(line)
              if (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] == "email_recipient")):
                 oldEmails = parts[4].split(",")

        activeSection = 0;
        skip = 0;
        # Write contents to file.
        # Using mode 'w' truncates the file.
        file_handle = open("/etc/mymail.conf", 'w')
        for line in file_string.splitlines():
           if not line.isspace():
              parts = findConfigLine(line)
              if (activeSection == 1):
                  if (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] in oldEmails)):
                      #skip line to delete previous configuration
                      skip = 1
                  else:
                      #lets write the new configuration
                      for email in notifications.keys():
                          if (notifications[email][0].strip() != ""):
                             line = email + " = " + notifications[email][0] + "\n" + line
                      activeSection = 0
              elif (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] == "email_recipient")):
                  myList = list(parts)
                  myList[1] = ""
                  myList[4] = notifications_order_string
                  line = "".join(myList)
                  activeSection = 1;
           else:
              if (activeSection == 1):
                 for email in notifications.keys():
                    if (notifications[email][0].strip() != ""):
                       line = email + " = " + notifications[email][0] + "\n" + line
                 activeSection = 0

           if (skip == 0):
             file_handle.write(line+"\n")
           skip = 0

        file_handle.close()
        #MyClientInterface.ProcessMonitorCommand("generator: reload")
        #Reload()
        Restart()

    except Exception as e1:
        log.error("Error Update Config File: " + str(e1))

#------------------------------------------------------------
def GetSettings():

    ### array containing information on the parameters
    ## 1st: type of attribute
    ## 2nd: Attribute title
    ## 3rd: Sort Key
    ## 4th: current value (will be populated further below)
    ## 5th: tooltip (will be populated further below)
    ## 6th: parameter is currently disabled (will be populated further below)

    allSettings =  {
                    "sitename" : ['string', 'Site Name', 1],
                    "port" : ['string', 'Port for Serial Communication', 2],
                    "incoming_mail_folder" : ['string', 'Incoming Mail folder<br><small>(if IMAP enabled)</small>', 151],
                    "processed_mail_folder" : ['string', 'Mail Processed folder<br><small>(if IMAP enabled)</small>', 152],
                    # This option is not displayed as it will break the link between genmon and genserv
                    #"server_port" : ['int', 'Server Port', 5],
                    # this option is not displayed as this will break the modbus comms, only for debugging
                    #"address" : ['string', 'Modbus slave address', 6],
                    "loglocation" : ['string', 'Log Directory', 7],
                    #"alarmfile" : ['string', 'Alarm Descriptions', 9],
                    "displayoutput" : ['boolean', 'Output to Console', 50],
                    "displaymonitor" : ['boolean', 'Display Monitor Status', 51],
                    "displayregisters" : ['boolean', 'Display Register Status', 52],
                    "displaystatus" : ['boolean', 'Display Status', 53],
                    "displaymaintenance" : ['boolean', 'Display Maintenance', 54],
                    "enabledebug" : ['boolean', 'Enable Debug', 14],
                    "displayunknown" : ['boolean', 'Display Unknown Sensors', 15],
                    "disableoutagecheck" : ['boolean', 'Do not check for outages', 17],
                    # These settings are not displayed as the auto-detect controller will set these
                    # these are only to be used to override the auto-detect
                    #"uselegacysetexercise" : ['boolean', 'Use Legacy Exercise Time', 43],
                    #"liquidcooled" : ['boolean', 'Liquid Cooled', 41],
                    #"evolutioncontroller" : ['boolean', 'Evolution Controler', 42],
                    "petroleumfuel" : ['boolean', 'Petroleum Fuel', 40],
                    "outagelog" : ['string', 'Outage Log', 8],
                    "syncdst" : ['boolean', 'Sync Daylight Savings Time', 22],
                    "synctime" : ['boolean', 'Sync Time', 23],
                    "enhancedexercise" : ['boolean', 'Enhanced Exercise Time', 44],

                    # These do not appear to work on reload, some issue with Flask
                    "usehttps" : ['boolean', 'Use Secure Web Settings', 25],
                    "useselfsignedcert" : ['boolean', 'Use Self-signed Certificate', 26],
                    "keyfile" : ['string', 'https key file', 27],
                    "certfile" : ['string', 'https certificate File', 28],
                    "http_user" : ['string', 'Web user name', 29],
                    "http_pass" : ['string', 'Web password', 30],
                    # This does not appear to work on reload, some issue with Flask
                    "http_port" : ['int', 'Port of WebServer', 24],

                    "disableemail" : ['boolean', 'Disable Email usage', 101],
                    "email_pw" : ['string', 'Email Password', 103],
                    "email_account" : ['string', 'Email Account', 102],
                    "sender_account" : ['string', 'Sender Account', 104],
                    # "email_recipient" : ['string', 'Email Recepient<br><small>(comma delimited)</small>', 105], # will be handled on the notification screen
                    "smtp_server" : ['string', 'SMTP Server <br><small>(leave emtpy to disable)</small>', 106],
                    "imap_server" : ['string', 'IMAP Server <br><small>(leave emtpy to disable)</small>', 150],
                    "smtp_port" : ['int', 'SMTP Server Port', 107],
                    "ssl_enabled" : ['boolean', 'SMTP Server SSL Enabled', 108]}

    try:
       for configFile in ["/etc/mymail.conf", "/etc/genmon.conf"]:
           # Read contents from file as a single string
           file_handle = open(configFile, 'r')
           file_string = file_handle.read()
           file_handle.close()

           tooltip = ""

           for line in file_string.splitlines():
              if not line.isspace():
                 parts = findConfigLine(line)
                 if (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace())):
                    if parts[2] in allSettings:
                       allSettings[parts[2]].append(parts[4])
                       allSettings[parts[2]].append(tooltip)
                       tooltip = ""
                       if ((parts[1] is not None) and (not parts[1].isspace())):
                          allSettings[parts[2]].append(1)
                       else:
                          allSettings[parts[2]].append(0)
                 else:
                    parts = findCommentLine(line)
                    if (parts and (len(parts) >= 2)):
                       tooltip += parts[1] + " "

    except Exception as e1:
        log.error("Error Reading Config File (GetSettings): " + str(e1))

    return allSettings

#------------------------------------------------------------
def SaveSettings(query_string):
    settings = dict(urlparse.parse_qs(query_string, 1))

    try:
        for configFile in ["/etc/mymail.conf", "/etc/genmon.conf"]:
            # Read contents from file as a single string
            file_handle = open(configFile, 'r')
            file_string = file_handle.read()
            file_handle.close()

            # Write contents to file.
            # Using mode 'w' truncates the file.
            file_handle = open(configFile, 'w')
            for line in file_string.splitlines():
                if not line.isspace():
                    parts = findConfigLine(line)
                    for setting in settings.keys():
                        if (parts and (len(parts) >= 5) and parts[3] and (not parts[3].isspace()) and parts[2] and (parts[2] == setting)):
                            myList = list(parts)
                            if ((parts[1] is not None) and (not parts[1].isspace())):
                                # remove comment
                                myList[1] = ""
                            elif (parts[1] is None):
                                myList[1] = ""
                            myList[4] = settings[setting][0]
                            line = "".join(myList)
                file_handle.write(line+"\n")
            file_handle.close()
        #MyClientInterface.ProcessMonitorCommand("generator: reload")
        #Reload()
        Restart()
    except Exception as e1:
        log.error("Error Update Config File (SaveSettings): " + str(e1))


#------------------------------------------------------------
def findConfigLine(line):
    match = re.search(
        r"""^          # Anchor to start of line
        (\s*)          # $1: Zero or more leading ws chars
        (?:(\#\s*)?)   # $2: is it commented out
        (?:            # Begin group for optional var=value.
          (\S+)        # $3: Variable name. One or more non-spaces.
          (\s*=\s*)    # $4: Assignment operator, optional ws
          (            # $5: Everything up to comment or EOL.
            [^#\\]*    # Unrolling the loop 1st normal*.
            (?:        # Begin (special normal*)* construct.
              \\.      # special is backslash-anything.
              [^#\\]*  # More normal*.
            )*         # End (special normal*)* construct.
          )            # End $5: Value.
        )?             # End group for optional var=value.
        ((?:\#.*)?)    # $6: Optional comment.
        $              # Anchor to end of line""",
        line, re.MULTILINE | re.VERBOSE)
    if match :
      return match.groups()
    else:
      return []
#------------------------------------------------------------
def findCommentLine(line):
    match = re.search("^(\s*#\s*)(.*)$", line)
    if match :
      return match.groups()
    else:
      return []

#------------------------------------------------------------
# This will reload the Flask App if use_reloader = True is enabled on the app.run command
def Reload():
    #os.system('touch ' + AppPath)      # reloader must be set to True to use this
    try:
        log.error("Reloading: " + sys.executable + " " + __file__ )
        os.execl(sys.executable, 'python', __file__, *sys.argv[1:])
    except Exception as e1:
        log.error("Error in Reload: " + str(e1))

#------------------------------------------------------------
# This will restart the Flask App
def Restart():

    if not RunBashScript("startgenmon.sh restart"):
        log.error("Error in Restart")

#------------------------------------------------------------
def Update():
    # update
    if not RunBashScript("genmonmaint.sh updatenp"):   # update no prompt
        log.error("Error in Update")
    # now restart
    Restart()

#------------------------------------------------------------
def RunBashScript(ScriptName):
    try:
        pathtoscript = os.path.dirname(os.path.realpath(__file__))
        command = "/bin/bash "
        log.error("Script: " + command + pathtoscript + "/" + ScriptName)
        subprocess.call(command + pathtoscript + "/" + ScriptName, shell=True)
        return True

    except Exception as e1:
        log.error("Error in RunBashScript: " + str(e1))
        return False
#------------------------------------------------------------
# return False if File not present
def CheckCertFiles(CertFile, KeyFile):

    try:
        with open(CertFile,"r") as MyCertFile:
            with open(KeyFile,"r") as MyKeyFile:
                return True
    except Exception as e1:
        log.error("Unable to open Cert or Key file: " + str(e1))
        return False

    return True
#------------------------------------------------------------
def LoadConfig():

    global log
    global clientport
    global loglocation
    global bUseSecureHTTP
    global HTTPPort
    global HTTPAuthUser
    global HTTPAuthPass
    global SSLContext

    HTTPAuthPass = None
    HTTPAuthUser = None
    SSLContext = None
    try:
        config = RawConfigParser()
        # config parser reads from current directory, when running form a cron tab this is
        # not defined so we specify the full path
        config.read('/etc/genmon.conf')
        # heartbeat server port, must match value in check_generator_system.py and any calling client apps
        if config.has_option('GenMon', 'server_port'):
            clientport = config.getint('GenMon', 'server_port')

        if config.has_option('GenMon', 'loglocation'):
            loglocation = config.get("GenMon", 'loglocation')

        # log errors in this module to a file
        log = mylog.SetupLogger("genserv", loglocation + "genserv.log")

        if config.has_option('GenMon', 'usehttps'):
            bUseSecureHTTP = config.getboolean('GenMon', 'usehttps')

        if config.has_option('GenMon', 'http_port'):
            HTTPPort = config.getint('GenMon', 'http_port')

        # user name and password require usehttps = True
        if bUseSecureHTTP:
            if config.has_option('GenMon', 'http_user'):
                HTTPAuthUser = config.get('GenMon', 'http_user')
                HTTPAuthUser = HTTPAuthUser.strip()
                 # No user name or pass specified, disable
                if HTTPAuthUser == "":
                    HTTPAuthUser = None
                    HTTPAuthPass = None
                elif config.has_option('GenMon', 'http_pass'):
                    HTTPAuthPass = config.get('GenMon', 'http_pass')
                    HTTPAuthPass = HTTPAuthPass.strip()

        if bUseSecureHTTP:
            app.secret_key = os.urandom(12)
            OldHTTPPort = HTTPPort
            HTTPPort = 443
            if config.has_option('GenMon', 'useselfsignedcert'):
                bUseSelfSignedCert = config.getboolean('GenMon', 'useselfsignedcert')

                if bUseSelfSignedCert:
                    SSLContext = 'adhoc'
                else:
                    if config.has_option('GenMon', 'certfile') and config.has_option('GenMon', 'keyfile'):
                        CertFile = config.get('GenMon', 'certfile')
                        KeyFile = config.get('GenMon', 'keyfile')
                        if CheckCertFiles(CertFile, KeyFile):
                            SSLContext = (CertFile, KeyFile)    # tuple
                        else:
                            HTTPPort = OldHTTPPort
                            SSLContext = None
            else:
                # if we get here then usehttps is enabled but not option for useselfsignedcert
                # so revert to HTTP
                HTTPPort = OldHTTPPort

    except Exception as e1:
        log.error("Missing config file or config file entries: " + str(e1))

#------------------------------------------------------------
def ValidateFilePresent(FileName):
    try:
        with open(FileName,"r") as TestFile:     #
            return True
    except Exception as e1:
            log.error("File (%s) not present." % FileName)
            return False

#------------------------------------------------------------
if __name__ == "__main__":
    address='localhost' if len(sys.argv)<2 else sys.argv[1]

    AppPath = sys.argv[0]
    LoadConfig()

    log.error("Starting " + AppPath + ", Port:" + str(HTTPPort) + ", Secure HTTP: " + str(bUseSecureHTTP) + ", SelfSignedCert: " + str(bUseSelfSignedCert))

    # validate needed files are present
    file = os.path.dirname(os.path.realpath(__file__)) + "/startgenmon.sh"
    if not ValidateFilePresent(file):
        log.error("Required file missing")

    file = os.path.dirname(os.path.realpath(__file__)) + "/genmonmaint.sh"
    if not ValidateFilePresent(file):
        log.error("Required file missing")

    MyClientInterface = myclient.ClientInterface(host = address,port=clientport, log = log)

    while True:
        try:
            app.run(host="0.0.0.0", port=HTTPPort, threaded = True, ssl_context=SSLContext, use_reloader = False, debug = False)

        except Exception as e1:
            log.error("Error in app.run:" + str(e1))
            time.sleep(2)
            Restart()

