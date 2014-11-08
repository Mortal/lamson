import sys
import logging
logging.basicConfig(level=logging.DEBUG)
import argparse
import asyncore
import datetime
import threading
import traceback

import email
import smtpd

import lamson.server
import lamson.routing
import lamson.mail
# from lamson import queue, mail, routing


HANDLERS = []
relay = None


def handle_each(f):
    """
    Decorator to indicate that the function handles a received message.
    The function receives arguments message, address, host.
    """

    def f_all(message):
        for route_to in message.route_to:
            address, host = route_to.split('@')
            f(message, address, host)

    HANDLERS.append(f_all)
    return f


def handle_all(f):
    "Decorator to indicate that the function handles an envelope."
    HANDLERS.append(f)
    return f


def now_string():
    "Return the current date and time as a string."
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


def message_file(message):
    "Return an open file for writing the message to a file."
    i = -1
    s = 'message/%s.txt' % now_string()
    while True:
        try:
            return open(s, 'x')
        except FileExistsError:
            pass
        i += 1
        s = 'message/%s_%s.txt' % (now_string(), i)


@handle_all
def save_message(message):
    "Write the message to a file."
    with message_file(message) as fp:
        fp.write(str(message))


@handle_each
def handle(message, address, host):
    pass


def verbose_log(message):
    "Message handler for --log mode"
    now = datetime.datetime.now().strftime(' %Y-%m-%d %H:%M:%S ')
    print(now.center(79, '='))
    print(str(message))
    return True


def handle_error(message, handled, exceptions):
    # Message was handled by `handled`,
    # and `exceptions` threw exceptions.
    # Tell the administrators about this.
    print("An error occurred: %s handlers handled the message" % len(handled))
    for handler, exn in exceptions:
        print("Handler %s threw the exception:\n%s"
              % (handler.__name__, ''.join(traceback.format_exception(*exn))))


class SMTPReceiver(smtpd.SMTPServer):
    def __init__(self, host, port):
        self.host = host
        self.port = port
        super(SMTPReceiver, self).__init__((self.host, self.port), None)

    def process_message(self, Peer, From, To, Data):
        logging.debug(
            "Message received from Peer: %r, From: %r, to To %r."
            % (Peer, From, To))

        message = email.message_from_string(Data)
        handled = []
        exceptions = []
        for h in HANDLERS:
            try:
                if h(message):
                    handled.append(h)
            except:
                exceptions.append((h, sys.exc_info()))

        if not handled or exceptions:
            handle_error(message, handled, exceptions)


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
    receiver = SMTPReceiver(receiver_host, int(receiver_port))

    if args.log:
        HANDLERS[:] = [verbose_log]

    else:
        relay_host, relay_port = args.relay.split(':')
        global relay
        relay = lamson.server.Relay(
            host=relay_host,
            port=int(relay_port),
            debug=1,
        )

        # for module in (
        #     'TKMail',
        #     'TKlog',
        #     'TKcheckQueues',
        #     'TKTest',
        #     ):
        #     mod = __import__('app.handlers.%s' % module)
        #     HANDLERS.append(mod.START)

    # lamson.routing.Router.defaults(host='.+')
    # load_modules = [
    #     'app.handlers.TKMail', 'app.handlers.TKlog',
    #     'app.handlers.TKcheckQueues', 'app.handlers.TKTest'
    # ]
    # lamson.routing.Router.load(load_modules)

    poller = threading.Thread(
        target=asyncore.loop,
        kwargs={'timeout': 0.1, 'use_poll': True})
    poller.start()


if __name__ == "__main__":
    main()
