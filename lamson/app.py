#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: set ft=python sw=4 et:

import re
import sys
import string
import codecs
import logging

global relay

# from lamson import view
# from lamson.queue import Queue

# import pkg_resources ##required on my setup to use MySQLdb
# pkg_resources.require("MySQL-python")  ##required on my setup to use MySQLdb
import MySQLdb as mdb

# The year that the current BEST was elected (assumed <= 2056)
currentYear = 2014


class Error(Exception):
    """Base class of my own error types"""
    def __init__(self, msg):
        super(Error, self).__init__()
        self.msg = msg


class EvaluationError(Error):
    def __init__(self, msg):
        super(EvaluationError, self).__init__(msg)


class UserEvaluationError(EvaluationError):
    def __init__(self, msg):
        super(UserEvaluationError, self).__init__(msg)


class SystemEvaluationError(EvaluationError):
    def __init__(self, msg):
        super(SystemEvaluationError, self).__init__(msg)


class SpamError(Error):
    def __init__(self):
        super(SpamError, self).__init__('SpamError')


def addPrefixToSubject(message, prefix):
    """Add a prefix to the subject of a mail
    if the prefix is not already in the subject."""

    try:
        subject = message['Subject']
    except KeyError:
        return

    if prefix not in subject:
        # MailBase.__setitem__ might just append the header;
        # we want to replace it, so delete it to be sure
        del message['Subject']
        message['Subject'] = '%s %s' % (prefix.encode('utf-8'), subject)


def clearStatusHeaders(message):
    try:
        del message['X-TK-adresse']
    except KeyError:
        pass
    try:
        del message['X-TK-Error']
    except KeyError:
        pass


def setStatusHeaders(message, address, error):
    clearStatusHeaders(message)
    message['X-TK-adresse'] = address
    message['X-TK-Error'] = error


def emailErrorToAdmin(errorMsg, toAddr, message, mysql=None):
    """Send email to admin group due to some error.
    Includes the error in the email."""
    setStatusHeaders(message, address=toAddr.encode('utf-8'),
            error=errorMsg.encode('utf-8'))

    mailQueue = Queue("run/errorSent")
    mailQueue.push(message)

    emails = []
    if mysql:
        try:
            cur = mysql.cursor()
            cur.execute("""
                SELECT `tkfolk`.`email`
                FROM `tkfolk`, `grupper`,`gruppemedlemmer`
                WHERE `grupper`.`navn`='admin'
                AND `gruppemedlemmer`.`gruppeid`=`grupper`.`id`
                AND `gruppemedlemmer`.`personid`= `tkfolk`.`id`
            """)
            emailsset = cur.fetchall()
            for email in emailsset:
                emails.append(string.replace(str(email[0]), '&#064;', '@'))
        except mdb.Error, e: ##Database error => use static list.
            pass
    if not emails: ## If these are changed, remember to also update in TKcheckQueues
        emails = [
            "jonas@baeklund.info",
            "mads@baggesen.net",
            "adament@adament.net",
            "mathiasrav@gmail.com",
        ]
    addPrefixToSubject(message, "[TK-ERROR]")

    logging.error("ERROR: on address: " + str(toAddr) + " with errormessage " + errorMsg)
    for email in emails:
        relay.deliver(message, email)
        logging.debug("Error message sent to " + str(email))


prefixValues = {
    'K': -1,
    'G': 1,
    'B': 2,
    'O': 3,
    'T': 1
}


def getGrad(preFix, postFix, currentYear):
    """
    CurrentYear is the year where the current BEST was elected.
    Assumes currentyear <= 2056.
    Returnes the corresponding grad of pre,post and currentYear.
    (Calculates as the person have the prefix in year postfix)
    """
    if not postFix:
        grad = 0
    else:
        if len(postFix) == 4:
            first, second = int(postFix[0:2]), int(postFix[2:4])
            if (first + 1) % 100 != second:
                #There should be exactly one year between the two numbers
                raise UserEvaluationError(
                        "Not exactly one year between the years in the postfix: %s"
                        % postFix)
        year = int(postFix[0:2])
        if year > 56: ##19??
            grad = currentYear - (1900 + year)
        else: ##20??
            grad = currentYear - (2000 + year)

    ##Now evaluate the prefix:
    regexpRaised = re.compile(r"([KGBOT])([0-9]+)")
    i = 0
    while i < len(preFix):
        resul = regexpRaised.match(preFix[i:])
        if resul:
            grad += prefixValues[resul.group(1)] * int(resul.group(2))
            i += len(resul.group())
        else:
            grad += prefixValues[preFix[i]]
            i += 1
    return grad


def eval(address, mysql, setOfNotMatched, setOfSpam):
    """
    Evaluate each address which is divided by + and -.
    Collects the resulting sets of not matched and the set of spam addresses.
    And return the set of person indexes that are to receive the email.
    """

    personIdOps = []
    for sign, name in re.findall(r'([+-]?)([^+-]+)', address):
        try:
            personIds = evalAlias(name, mysql)
            personIdOps.append((sign or '+', personIds))
        except UserEvaluationError, e:
            setOfNotMatched.append("Address: %s UserError: %s\n"
                    % (name, e.msg))
        except SpamError, e:
            setOfSpam.append("Address: %s : Alias does not match any "
                    "group or generalized group\n"
                    % name)

    setOfIndex = set()
    for sign, personIds in personIdOps:
        if sign == '+': # union
            setOfIndex = setOfIndex.union(personIds)
        else: # minus
            setOfIndex = setOfIndex.difference(personIds)

    logging.debug("TKMail.py: size of setOfIndex: %d" % len(setOfIndex))

    return setOfIndex

def evalAlias(alias, mysql):
    """
    Evaluates the alias, returning a non-empty list of person IDs.
    Raise exception if a spam or no match email.
    """

    anciprefix = r"(?P<pre>(?:[KGBOT][KGBOT0-9]*)?)"
    ancipostfix = r"(?P<post>(?:[0-9]{2}|[0-9]{4})?)"
    try:
        cur = mysql.cursor()
        cur.execute("SELECT `id`,`regexp`,`relativ`,`type` FROM grupper")
        rows = cur.fetchall()
        matches = []
        for row in rows:
            groupId, groupRegexp, relativ, groupType = row
            groupId = int(groupId)
            if relativ == 1: ##Relativ = true
                regexp = (r'^%s(?P<name>%s)%s$'
                        % (anciprefix, groupRegexp, ancipostfix))
            else:
                regexp = '^(?P<name>%s)$' % groupRegexp
            result = re.match(regexp, alias)
            if result:
                matches.append((groupId, groupType, result))
        if not matches:
            raise SpamError() ##It is properly a spam mail
        if len(matches) > 1:
            raise SystemEvaluationError(
                    "The alias:'%s' matches more than one group"
                    % alias)

        groupId, groupType, result = matches[0]

        if groupType == 0:##Group, without aging
            logging.debug("TKMail.py: " + str(result.group("name")))
            cur.execute("""
                SELECT `personid` FROM `gruppemedlemmer`
                WHERE `gruppeid`='%s'
                """ % groupId)
        elif groupType == 1:##Group with aging
            logging.debug("TKMail.py: %s pre: %s post: %s"
                    % (result.group('name'), result.group('pre'),
                        result.group('post')))
            grad = getGrad(result.group("pre"),
                    result.group("post"),
                    currentYear)
            cur.execute("""
                SELECT `personid` FROM `gradgruppemedlemmer`
                WHERE `gruppeid`='%s' AND `grad`='%s'
                """ % (groupId, grad))
        elif groupType == 2:##Titel, with/without aging
            logging.debug("TKMail.py: %s pre: %s post: %s"
                    % (result.group('name'), result.group('pre'),
                        result.group('post')))
            grad = getGrad(result.group("pre"),
                    result.group("post"),
                    currentYear)
            cur.execute("""
                SELECT `personid` FROM `titler`
                WHERE `inttitel`='%s' AND `grad`='%s'
                """ % (result.group('name'), grad))
        elif groupType == 3:##Direct user
            logging.debug("TKMail.py: " + result.group("name"))
            cur.execute("""
                SELECT `id` FROM `tkfolk`
                WHERE `id`='%s'
                """ % result.group('name')[6:])
        elif groupType == 4:##BESTFU hack
            logging.debug("TKMail.py: " + result.group("name"))
            grad = getGrad(result.group("pre"),
                    result.group("post"),
                    currentYear)
            cur.execute("""
                SELECT `personid` FROM `gradgruppemedlemmer`
                WHERE (`gruppeid`='%s'
                    OR `gruppeid`='%s' )
                AND `grad`='%s'
                """ % (groupId + 1, groupId - 1, grad))
        else:
            raise SystemEvaluationError(
                    "Error in table gruppe, type: %s is unknown."
                    % groupType)


        personIds = list(cur.fetchall())
        if not personIds:
            ##No users in the database fit the current alias
            raise UserEvaluationError(
                    "No users are registered for this alias: %s"
                    % str(alias))

        return personIds

    except mdb.Error, e:
        logging.error("TKMail.py:Evalalias(): Error %d: %s"
                % (e.args[0], e.args[1]))
        raise


def START(message, address=None, host=None):
    ### Reload local import files, so changes are seen.
    ### When not debugging this should properly not be performed.
    #reload(sys.modules["app.handlers.aqueue"]) #Not using local import anymore


    logging.info("Mail received to: '%s' at host: '%s'" % (address, host))
    origAddress = address

    ### Might exist because of the TKlog, or because the mail is send to more than one @TK.dk address.
    clearStatusHeaders(message)



##For debugging only:
   # logging.debug(str(message.keys()))
   # for key in message.keys():
   #     logging.debug(message.__getitem__(key))
   # logging.debug("----")
   # for part in message.all_parts():
   #     logging.debug(part)
   # logging.debug(type(message))
   # logging.debug(message.body())

   # logging.debug("----route_to")
   # for route in message.route_to:
   #     logging.debug(route)
   # logging.debug("----")


##Connect to MySQLDB
    try:
        #(host,username,pass,db)
        mysql = mdb.connect('localhost', 'tkammer', 'YbER9z6H3fm2', 'tkammer')
    except mdb.Error, e:
        emailErrorToAdmin("TKMail.py:mdb.connect: Error %d: %s"
                % (e.args[0], e.args[1]),
                address+"@"+host, message)
        return




    legalhosts = set(( ##Assumes all hosts are entered as lowercase
        "lamson.spet.dk", ##FIXME
        "localhost", ##FIXME
        "taagekammeret.dk",
    ))

    ## Only mails for legal hosts are considered.
    if host.lower() not in legalhosts:
        errorMsg = ("TKMail.py: Mail not in legalhost => quitting : "
            "Email in pushed in the SPAM queue.")
        logging.info(errorMsg)
        setStatusHeaders(message,
                address=(origAddress+"@"+host).encode('utf-8'),
                error=errorMsg.encode('utf-8'))

        spamQueue = Queue("run/spam")
        spamQueue.push(message)
        return


    ##Trying to convert danish letters:

    address = string.replace(address, '\xc3\xa6', 'AE')##ae
    address = string.replace(address, '\xc3\x86', 'AE')##AE

    address = string.replace(address, '\xc3\xa5', 'AA')##aa
    address = string.replace(address, '\xc3\x85', 'AA')##AA

    address = string.replace(address, '\xc3\xb8', 'OE')##oe
    ## This seems very odd, but what is received when connecting from
    ## Thunderbird to the server
    address = string.replace(address, '\xc3', 'OE')##OE

    address = string.replace(address, '$', 'S')## Remove the dollar sign.
    address = address.upper()##All titles and groups is uppercase in the database

    ##Removing " if they sorround the address
    if address[0] == '"' and address[-1] == '"':
        address = address[1:-1]
    logging.info("TKMail.py: Address converted to: '%s' at host: '%s'"
            % (address, host))



    ##Check if the address only consist of legal characters A-Z 0-9  - + * _
    ## + - are special characters so they should not be used for anything else.
    addressRegExp = re.compile(r'^[A-Z0-9\-*_+]+$')
    if not addressRegExp.match(address):
        s = ("TKMail.py: Address consist of illegal caracters: "
            "Orig address: '%s' which was converted to '%s'"
            % (origAddress, address))
        logging.error(s)

        ##fixme perhaps it should just be put in the spam queue.
        emailErrorToAdmin(s, address+"@"+host, message, mysql)

        return

    setOfEmails = []
    setOfNotMatched = []
    setOfSpam = []
    try:
        setOfEmails = eval(address, mysql, setOfNotMatched, setOfSpam)
    except mdb.Error, e:
        emailErrorToAdmin("TKMail.py: Error %d: %s"
                % (e.args[0], e.args[1]),
                address+"@"+host, message, mysql)
        return
    except SystemEvaluationError, e:
        emailErrorToAdmin("TKMail.py: SystemEvaluationError "
            "Orig address: '%s' which was converted to '%s' Error: %s"
            % (origAddress, address, e.msg),
            address+"@"+host, message, mysql)
        return



    if setOfSpam:
        error = ("TKMail.py: Alias does not match any group or generalized group: "
            "%s => : Email in pushed in the SPAM queue." % ', '.join(setOfSpam))
        logging.info(error)

        setStatusHeaders(message,
                address=(origAddress+"@"+host).encode('utf-8'),
                error=error.encode('utf-8'))

        spamQueue = Queue("run/spam")
        spamQueue.push(message)
        clearStatusHeaders(message)


    if setOfNotMatched:
        emailErrorToAdmin("TKMail.py: Could not match the following: %s"
                % ", ".join(setOfNotMatched),
                address+"@"+host, message, mysql)



    if setOfEmails:
        strListOfIndex = ""
        for personid in setOfEmails:
            strListOfIndex += str(personid[0]) + ","
        strListOfIndex = strListOfIndex[:-1] ##remove trailing
        try:
            addPrefixToSubject(message, "[TK]")
        except EvaluationError, e:
            emailErrorToAdmin("TKMail.py: " + e.msg,
                    address+"@"+host, message, mysql)
            return

        try:
            cur = mysql.cursor()
            cur.execute("SELECT `email` FROM `tkfolk` WHERE `id` IN (%s) "
                "AND `accepterdirektemail`='Ja'" % strListOfIndex)
            emails = cur.fetchall()
            if not emails:
                emailErrorToAdmin("TKMail.py: No person in the list accepts emails",
                        address+"@"+host, message, mysql)
                return
            for emailrow in emails:
                email = string.replace(str(emailrow[0]), '&#064;', '@')
                if len(str(email)) > 3:
                    logging.info("Email with address: %s@%s sent to email: %s"
                            % (address, host, email))
                    relay.deliver(message, str(email))

        except mdb.Error, e:
            emailErrorToAdmin("TKMail.py: mdb.Error %d: %s"
                    % (e.args[0], e.args[1]),
                    address+"@"+host, message, mysql)
            return



    if mysql:
        mysql.close()


