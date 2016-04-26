#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
html_footer.py

This script looks for special formated plain messages and convert them
into html. It is intended to use this script as a postfix message filter.

It can be used as a pipe or as a standalone stmp daemon application.

Usage: html_footer.py [OPTION...]

    -h, --help             show this help message
    -V, --version          shows version information
    -u, --uid=USERNAME     run as uid if in daemon mode
    -p, --pipemode         read/write message from/to stdin/stdout
    -d, --debuglevel=LEVEL default level = info
                           valid levels: critical, error, warning,
                                         info, debug
    -l, --listen=HOST:IP   port to listen on (default: 127.0.0.1:10025)
    -r, --remote=HOST:IP   relayhost to deliver to (default: 127.0.0.1:25)
    -i, --imagepath=PATH   path for attachments (default: /var/lib/html_footer)
    -f, --logfile=FILENAME
    -k, --kill             kills daemon
    -p, --pidfile=FILENAME pidfile for daemon (default:
                                               /var/run/html_footer.pid)

The decision if a mail has to be converted is taken by a line with the
tags <html> </html> in the signature of the plain mail.

Example:
-----8<-----
Dear ..

best regards
--
Text signature
<html>
<hr/>
<p>
Html signature
</p>
</html>
-----8<-----

If image tags a refered in html attachment text, the should be placed in
the directory IMG_PATH on the machine the script is running on.
The use of inline encoded data is also possible.
The img tag is only recognized if it doesn't span over a linebreak.
The src-attributes content should be prefixed with file: or without
any protocol directive. eg. <img src="logo.png">

@copyright: 2012 dass IT GmbH, 2013 Holger Mueller
@author: Holger Mueller <zarath@gmx.de>>
'''
import logging
from pprint import pformat
import sys
import os
import errno
import getopt

import email
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.charset import Charset
from email.utils import make_msgid

from smtpd import PureProxy
import asyncore

import re
from urlparse import urlparse

from daemon import Daemon
# Insert modification in email header
X_HEADER = True

#
# Nothing to configure below!
#
__version__ = "20120227"


def txt2html(txt=u""):
    """helper function to preformat plain text"""
    html = u'<pre id="plaintext">\n'
    html += txt
    html += u'</pre>\n'
    return html


def payload2unicode(mimeobj):
    """convert MIME text objects to unicode string"""
    chrset = mimeobj.get_content_charset(Charset())
    return unicode(mimeobj.get_payload(decode=True).decode(chrset))


class HyperTextFormatter(object):
    '''Parse plain text and generate hypertext'''

    HTML_HEADER = \
        u'''<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"
    "http://www.w3.org/TR/html4/loose.dtd">
<head>
<meta http-equiv="content-type" content="text/html; charset=UTF-8">
<style type="text/css">
#plaintext      {
    font-family:Fixedsys,Courier,monospace;
    padding:10px;
    white-space:pre-wrap;
}
</style>
</head>
<body>
'''
    HTML_FOOTER = \
        u'''</body>\n</html>'''
    # Regex for referal of image attachements
    RXP_IMG_TAG = re.compile(ur'(<img\s[^>]*src=")([^"]+)("[^>]*>)',
                             re.UNICODE)

    def __init__(self, header=u''):
        """initialize the class,
           a custom html header could be supplied
        """
        if header != u'':
            self.txt += header
        else:
            self.txt = self.HTML_HEADER
        self.attachments = []
        self.parts = 1

    def add_txt(self, txt=u''):
        """add plain text and wrap it to html"""
        self.txt += txt2html(txt)

    def add_html(self, html=u''):
        """add html text without modification"""
        self.txt += html

    def add_footer(self):
        """extends the current html text with the default footer"""
        self.txt += self.HTML_FOOTER

    def create_mime_attachments(self):
        """scans current html text, creates a MIME object for every
           referenced image and replace src-attribute with a cid:
           reference to the generated MIME objects.
           Returns the list of generated MIME objects.
        """

        def replacer(match):
            """callback function for re.sub"""
            path = urlparse(match.group(2))[2]
            filename = os.path.join(options.imagepath,
                                    os.path.split(path)[1])
            imgfp = open(filename, 'rb')
            img = MIMEImage(imgfp.read())
            img_id = make_msgid("part%i" % self.parts)
            img.add_header('Content-ID', img_id)
            img.add_header('Content-Disposition',
                           'attachment',
                           filename=path)
            self.attachments.append(img)
            self.parts += 1
            return "%scid:%s%s" % (match.group(1),
                                   img_id.strip('<>'),
                                   match.group(3))

        self.txt = self.RXP_IMG_TAG.sub(replacer, self.txt)
        return self.attachments

    def get(self, add_footer=True):
        """returns htmlized email message"""
        if add_footer:
            self.add_footer()
        return self.txt

    def has_attachments(self):
        """returns True if img tags with file: or no protocol extension
           found in current html text
        """
        match = self.RXP_IMG_TAG.search(self.txt)
        if match:
            scheme, path = urlparse(match.group(2))[0, 3, 2]
            if path and (not scheme or scheme == "file"):
                return True
        return False


def copy_mime_root(msg, strip_content=True):
    """Make a copy of the non_payload root mime part of msg and change
    content type to multipart/alternativ. By default drop old Content- headers.
    """

    msg_new = MIMEMultipart()
    # drop default keys
    for k in msg_new.keys():
        del msg_new[k]

    # make copy of old header
    for k, v in msg.items():
        if strip_content and k.startswith('Content-'):
            continue
        msg_new[k] = v

    if msg.get_unixfrom():
        msg_new.set_unixfrom(msg.get_unixfrom())
    if msg.preamble:
        msg_new.preamble = msg.preamble
    else:
        msg_new.preamble = "This is a multi-part message in MIME format...\n"
    if msg.epilogue:
        msg_new.epilogue = msg.epilogue

    # set msg_new type
    msg_new.set_type('multipart/alternative')
    return msg_new


def first_text(msg):
    """returns first text/plain part of a message as unicode string"""
    if not msg.is_multipart():
        if msg.get_content_type() != 'text/plain':
            return u''
        else:
            return payload2unicode(msg)
    else:
        for match in msg.get_payload():
            if match.get_content_type() == 'text/plain':
                return payload2unicode(match)
    return u''


class MIMEChanger(object):
    """
    This class actually changes email's mime structure
    """

    # Regex to split message from signature
    RXP_SIGNATURE = re.compile(r'(.*)^--\s+(.*)',
                               re.MULTILINE | re.DOTALL | re.UNICODE)
    RXP_SIG_HTML = re.compile(ur'^<html>\n', re.MULTILINE | re.UNICODE)

    def _process_multi(self, msg):
        """multipart messages can be changend in place"""
        # find the text/plain mime part in payload
        i = 0
        pload = msg.get_payload()
        for msgpart in pload:
            if msgpart.get_content_type() == 'text/plain':
                break
            i += 1

        # change it to the new payload
        pload[i] = self.new_payload(pload[i])
        return msg

    def _process_plain(self, msg):
        """make container for plain messages"""
        msg_new = copy_mime_root(msg)
        new_pl = self.new_payload(msg)
        for msgpart in new_pl.get_payload():
            msg_new.attach(msgpart)

        return msg_new

    def _split_content(self, txt=u''):
        """Cuts content from signature of mail message"""
        match = self.RXP_SIGNATURE.search(txt)
        if match:
            return match.groups()
        else:
            return [txt, u'']

    def _split_signature(self, txt=u''):
        """Cuts txt and html part of signature text"""
        return self.RXP_SIG_HTML.split(txt, 1)

    def alter_message(self, msg):
        """message modification function"""
        if not msg.is_multipart():
            log.debug('plain message')
            new_msg = self._process_plain(msg)
        else:
            log.debug('multipart message')
            new_msg = self._process_multi(msg)

        if X_HEADER:
            log.debug('add X-Modified-By header')
            new_msg.add_header('X-Modified-By', 'Html Footer %s' % __version__)
        return new_msg

    def html_creator(self):
        """returns a HyperTextFormatter instance, can be overloaded
           in derived class for better layout creation"""
        return HyperTextFormatter()

    def msg_is_to_alter(self, msg):
        """check if message should be altered
        in this special case we look for a html/xml tag in the
        beginning of a line in the the first text/plain mail parts signature
        """
        txt = first_text(msg)
        sig = self._split_content(txt)[1]

        return self.RXP_SIG_HTML.search(sig)

    def new_payload(self, mime_plain):
        """create a new mime structure from text/plain
           Examples:
           multipart/alternative
             text/plain
             text/html

           multipart/alternative
             text/plain
             multipart/related
                 text/html
                 image/jpg
                 image/png
        """

        html = self.html_creator()

        chrset = mime_plain.get_content_charset(Charset())
        content = unicode(mime_plain.get_payload(decode=True), chrset)

        text, signature = self._split_content(content)
        html.add_txt(text)
        text += u'-- \n'

        # strip html from signature
        text += self._split_signature(signature)[0]

        state_html = True
        footer = u''
        txtbuffer = u''
        try:
            footer = self._split_signature(signature)[1]
        except IndexError:
            pass
        for line in footer.split(u'\n'):
            if line == u'<html>':
                state_html = True
                if txtbuffer:
                    html.add_txt(txtbuffer)
                    txtbuffer = u''
            elif line == u'</html>':
                state_html = False
            else:
                if state_html:
                    html.add_html(line + u'\n')
                else:
                    txtbuffer += line + u'\n'
                    text += line + u'\n'
        if txtbuffer:
            html.add_txt(txtbuffer)

        if html.has_attachments():
            attachments = html.create_mime_attachments()
            msg_html = MIMEMultipart('related')
            msg_html.attach(
                MIMEText(html.get().encode('utf-8'), 'html', 'utf-8'))
            for att in attachments:
                msg_html.attach(att)
        else:
            msg_html = MIMEText(html.get().encode('utf-8'), 'html', 'utf-8')

        msg_plain = MIMEText(text.encode('utf-8'), 'plain', 'utf-8')

        pload = MIMEMultipart('alternative')
        pload.attach(msg_plain)
        pload.attach(msg_html)

        return pload


class SMTPHTMLFooterServer(PureProxy):
    """Python's SMTP implementation"""
    def process_message(self, peer, mailfrom, rcpttos, data):
        # TODO return error status (as SMTP answer string)
        # if something goes wrong!
        try:
            data = modify_data(data)
            refused = self._deliver(mailfrom, rcpttos, data)
        except Exception as err:
            log.exception('Error on delivery: %s', err)
            return '550 content rejected: %s' % err
        # TODO: what to do with refused addresses?
        # print >> DEBUGSTREAM, 'we got some refusals:', refused
        if refused:
            log.error('content refused: %s', pformat(refused))
            return '550 content rejected:'


class FooterDaemon(Daemon):
    def run(self):
        asyncore.loop()


class Options:
    uid = ''
    listen = ('127.0.0.1', 10025)
    remote = ('127.0.0.1', 25)
    debuglevel = logging.INFO
    cmd = 'start'
    pipemode = False
    pidfile = '/var/run/hmtl_footer.pid'
    imagepath = '/var/lib/html_footer'
    logfile = ''
    txt2loglvl = {
        'critical': logging.CRITICAL,
        'error': logging.ERROR,
        'warning': logging.WARNING,
        'info': logging.INFO,
        'debug': logging.DEBUG,
    }


def usage(code, msg=''):
    print >> sys.stderr, __doc__ % globals()
    if msg:
        print >> sys.stderr, msg
    sys.exit(code)


def parseargs():
    try:
        opts, args = getopt.getopt(
            sys.argv[1:], 'u:Vhpd:l:r:i:f:kp:',
            ['uid=', 'version', 'help', 'pipemode', 'debuglevel=',
             'listen=', 'remote=', 'imagepath=', 'logfile=',
             'kill', 'pidfile='])
    except getopt.error as err:
        usage(1, err)

    options = Options()
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            usage(0)
        elif opt in ('-V', '--version'):
            print >> sys.stderr, __version__
            sys.exit(0)
        elif opt in ('-u', '--uid'):
            options.uid = arg
        elif opt in ('-p', '--pipemode'):
            options.pipemode = True
        elif opt in ('-d', '--debuglevel'):
            if arg in options.txt2loglvl.keys():
                options.debuglevel = options.txt2loglvl[arg]
            else:
                usage(1, 'Unknown debuglevel %s' % arg)
        elif opt in ('-l', '--listen'):
            i = arg.find(':')
            if i < 0:
                usage(1, 'Bad listen address: %s' % arg)
            try:
                options.listen = (arg[:i], int(arg[i+1:]))
            except ValueError:
                usage(1, 'Bad local port: %s' % arg)
        elif opt in ('-r', '--remote'):
            i = arg.find(':')
            if i < 0:
                usage(1, 'Bad remote address: %s' % arg)
            try:
                options.remote = (arg[:i], int(arg[i+1:]))
            except ValueError:
                usage(1, 'Bad remote port: %s' % arg)
        elif opt in ('-i', '--imagepath'):
            options.imagepath = arg
        elif opt in ('-f', '--logfile'):
            options.logfile = arg
        elif opt in ('-k', '--kill'):
            options.cmd = 'stop'
        elif opt in ('-p', '--pidfile'):
            options.pidfile = arg
        if len(args) > 0:
            usage(1, 'unknown arguments %s' % ', '.join(args))

    return options


def modify_data(msg_in):
    msg = email.message_from_string(msg_in)
    if mymime.msg_is_to_alter(msg):
        log.info('Msg(%s): altered', msg.get('Message-ID', ''))
        msg = mymime.alter_message(msg)
        log.debug('Msg out:\n%s', msg.as_string(unixfrom=True))
        return msg.as_string(unixfrom=True)
    else:
        log.info('Msg(%s): nothing to alter', msg.get('Message-ID', ''))
        return msg_in

#
# Main program
#
if __name__ == '__main__':
    options = parseargs()
    logging.basicConfig(level=options.debuglevel, filename=options.logfile)
    log = logging.getLogger('html_footer')

    # use as simple pipe filter
    if options.pipemode:
        msg_in = sys.stdin.read()
        log.debug('Msg in:\n%s', msg_in)
        try:
            mymime = MIMEChanger()
            msg_out = modify_data(msg_in)
            log.debug('Msg out:\n%s', msg_out)
            sys.stdout.write(msg_out)
        except Exception as err:
            log.exception(err)
            sys.stdout.write(msg_in)
    # run as smtpd
    else:
        mymime = MIMEChanger()
        daemon = FooterDaemon(options.pidfile)
        if options.cmd == 'stop':
            log.info('stopping daemon')
            daemon.stop()
            sys.exit(0)

        log.info('starting daemon')
        if options.uid:
            try:
                import pwd
            except ImportError:
                log.exception('''Cannot import module "pwd";
try running as pipe filter (-p).''')
                sys.exit(1)
            runas = pwd.getpwnam(options.uid)[2]
            try:
                os.setuid(runas)
            except OSError as err:
                if err.errno != errno.EPERM:
                    raise  # what else can happen?
                log.exception('''Cannot setuid "%s";
try running as pipe filer (-p).''', options.uid)
                sys.exit(1)
        log.debug('Creating server instance')
        server = SMTPHTMLFooterServer(options.listen, options.remote)
        # if uid is given daemonize
        if options.uid:
            daemon.start()
        else:
            asyncore.loop()
