import sys
import json
import logging
logging.basicConfig(level=logging.DEBUG)
import smtplib
import argparse
import asyncore
import datetime
import threading
import traceback

import email
import email.message
from email.mime.base import MIMEBase
import smtpd


def now_string():
    """Return the current date and time as a string."""
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")


class Message(object):
    def __init__(self, message):
        assert isinstance(message, email.message.Message)
        self.message = message

    def __str__(self):
        return str(self.message)

    def add_header(self, key, value):
        pass

    def get_unique_header(self, key):
        pass

    def set_unique_header(self, key, value):
        pass

    @property
    def subject(self):
        return self.get_unique_header('Subject')

    @subject.setter
    def subject(self, s):
        self.set_unique_header('Subject', s)


class Envelope(object):
    def __init__(self, message, mailfrom, rcpttos):
        assert isinstance(message, Message)
        self.message = message
        self.mailfrom = mailfrom
        self.rcpttos = rcpttos


class SMTPReceiver(smtpd.SMTPServer):
    def __init__(self, host, port):
        self.host = host
        self.port = port
        super(SMTPReceiver, self).__init__((self.host, self.port), None)

    def process_message(self, peer, mailfrom, rcpttos, Data):
        message = Message(email.message_from_string(Data))
        if Data.rstrip('\n') == str(message.message).rstrip('\n'):
            logging.debug("%s Data is sane" % self.port)
        else:
            logging.debug("%s Data is not sane" % self.port)
            print(repr(Data))
            print(repr(str(message.message)))
        envelope = Envelope(message, mailfrom, rcpttos)
        logging.debug(
            "Message received from Peer: %r, From: %r, to To %r."
            % (peer, mailfrom, rcpttos))
        try:
            return self.handle_envelope(envelope)
        except:
            try:
                self.handle_error(envelope)
            except:
                logging.exception("handle_error threw exception")

            # Instruct the sender to retry sending the message later.
            return '451 Requested action aborted: error in processing'

    def handle_envelope(self, envelope):
        raise NotImplementedError()

    def handle_error(self, envelope):
        logging.exception("Could not handle envelope!")


class LoggingReceiver(SMTPReceiver):
    """Message handler for --log mode"""

    def handle_envelope(self, envelope):
        now = datetime.datetime.now().strftime(' %Y-%m-%d %H:%M:%S ')
        print(now.center(79, '='))
        print(str(envelope.message))


class RelayMixin(object):
    def configure_relay(self):
        # relay_host = smtplib.SMTP_SSL(hostname, self.port)
        relay_host = smtplib.SMTP(self.relay_host, self.relay_port)
        relay_host.set_debuglevel(0)
        # relay_host.starttls()
        # relay_host.login(self.username, self.password)
        return relay_host

    def deliver(self, message, recipient, sender):
        relay_host = self.configure_relay()
        try:
            relay_host.sendmail(sender, recipient, str(message))
        finally:
            relay_host.quit()


class SMTPForwarder(SMTPReceiver, RelayMixin):
    def __init__(self, receiver_host, receiver_port, relay_host, relay_port):
        super(SMTPForwarder, self).__init__(receiver_host, receiver_port)
        self.relay_host = relay_host
        self.relay_port = relay_port

    def handle_envelope(self, envelope):
        self.deliver(envelope.message, envelope.rcpttos, envelope.mailfrom)

    def handle_error(self, envelope):
        tb = ''.join(traceback.format_exc())
        now = now_string()

        with open('error/%s.mail' % now, 'w') as fp:
            fp.write(str(envelope.message))

        with open('error/%s.json' % now, 'w') as fp:
            metadata = {
                'mailfrom': envelope.mailfrom,
                'rcpttos': envelope.rcpttos,
            }
            json.dump(metadata, fp)

        with open('error/%s.txt' % now, 'w') as fp:
            fp.write('From %s\n' % envelope.mailfrom)
            fp.write('To %s\n\n' % envelope.rcpttos)
            fp.write('%s\n' % tb)
            fp.write(str(envelope.message))


def validate_address(v):
    host, port = v.split(':')
    port = int(port)
    return host, port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--listen',
        type=validate_address,
        default=('0.0.0.0', 9000),
        help='hostname and port to listen on (default 0.0.0.0:9000)',
    )
    parser.add_argument(
        '--relay',
        type=validate_address,
        default=('127.0.0.1', 25),
        help='hostname and port to relay to (default 127.0.0.1:25)',
    )
    parser.add_argument(
        '--log',
        action='store_true',
        help='instead of relaying email, log all received',
    )
    args = parser.parse_args()
    if not args.log and not args.relay:
        parser.error("Must specify either --relay or --log")

    receiver_host, receiver_port = args.listen

    if args.log:
        server = LoggingReceiver(receiver_host, receiver_port)

    else:
        relay_host, relay_port = args.relay
        server = SMTPForwarder(
            receiver_host, receiver_port,
            relay_host, relay_port)

    poller = threading.Thread(
        target=asyncore.loop,
        kwargs={'timeout': 0.1, 'use_poll': True})
    poller.start()


if __name__ == "__main__":
    main()
