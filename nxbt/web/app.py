import json
import os
from threading import RLock
import time
from socket import gethostname

from .cert import generate_cert
from ..nxbt import Nxbt, PRO_CONTROLLER
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import eventlet
import socketio


app = Flask(__name__,
            static_url_path='',
            static_folder='static',)
nxbt = Nxbt()

# Configuring/retrieving secret key
secrets_path = os.path.join(
    os.path.dirname(__file__), "secrets.txt"
)
if not os.path.isfile(secrets_path):
    secret_key = os.urandom(24).hex()
    with open(secrets_path, "w") as f:
        f.write(secret_key)
else:
    secret_key = None
    with open(secrets_path, "r") as f:
        secret_key = f.read()
app.config['SECRET_KEY'] = secret_key

# Starting socket server with Flask app
sio = SocketIO(app, cookie=False)

user_info_lock = RLock()
USER_INFO = {}


@app.route('/')
def index():
    return render_template('index.html')


@sio.on('connect')
def on_connect():
    with user_info_lock:
        USER_INFO[request.sid] = {}


@sio.on('state')
def on_state():
    state_proxy = nxbt.state.copy()
    state = {}
    for controller in state_proxy.keys():
        state[controller] = state_proxy[controller].copy()
    emit('state', state)


@sio.on('disconnect')
def on_disconnect():
    print("Disconnected")
    with user_info_lock:
        try:
            index = USER_INFO[request.sid]["controller_index"]
            nxbt.remove_controller(index)
        except KeyError:
            pass


@sio.on('shutdown')
def on_shutdown(index):
    nxbt.remove_controller(index)


@sio.on('create_pro_controller')
    def on_create_controller(self, index, controller_type, adapter_path,
                          colour_body=None, colour_buttons=None,
                          reconnect_address=None):
        """Instantiates a given controller as a multiprocessing
        Process with a shared state dict and a task queue.

        Configuration options are available in the form of
        controller colours.

        :param index: The index of the controller
        :type index: int
        :param controller_type: The type of Nintendo Switch controller
        :type controller_type: ControllerTypes
        :param adapter_path: The DBus path to the Bluetooth adapter
        :type adapter_path: str
        :param colour_body: A list of three ints representing the hex
        colour of the controller, defaults to None
        :type colour_body: list, optional
        :param colour_buttons: A list of three ints representing the
        hex colour of the controller, defaults to None
        :type colour_buttons: list, optional
        :param reconnect_address: The address of a Nintendo Switch
        to reconnect to, defaults to None
        :type reconnect_address: str, optional
        """

        controller_queue = Queue()

        controller_state = self.controller_resources.dict()
        controller_state["state"] = "initializing"
        controller_state["finished_macros"] = []
        controller_state["errors"] = False
        controller_state["direct_input"] = json.loads(json.dumps(DIRECT_INPUT_PACKET))
        controller_state["colour_body"] = colour_body
        controller_state["colour_buttons"] = colour_buttons
        controller_state["type"] = str(controller_type)
        controller_state["adapter_path"] = adapter_path
        controller_state["last_connection"] = None

        self._controller_queues[index] = controller_queue

        self.state[index] = controller_state

        server = ControllerServer(controller_type,
                                  adapter_path=adapter_path,
                                  lock=self.lock,
                                  state=controller_state,
                                  task_queue=controller_queue,
                                  colour_body=colour_body,
                                  colour_buttons=colour_buttons)
        controller = Process(target=server.run, args=(reconnect_address,))
        controller.daemon = True
        self._children[index] = controller
        controller.start()


@sio.on('input')
def handle_input(message):
    # print("Webapp Input", time.perf_counter())
    message = json.loads(message)
    index = message[0]
    input_packet = message[1]
    nxbt.set_controller_input(index, input_packet)


@sio.on('macro')
def handle_macro(message):
    message = json.loads(message)
    index = message[0]
    macro = message[1]
    nxbt.macro(index, macro)


def start_web_app(ip='0.0.0.0', port=8000, usessl=False, cert_path=None):
    if usessl:
        if cert_path is None:
            # Store certs in the package directory
            cert_path = os.path.join(
                os.path.dirname(__file__), "cert.pem"
            )
            key_path = os.path.join(
                os.path.dirname(__file__), "key.pem"
            )
        else:
            # If specified, store certs at the user's preferred location
            cert_path = os.path.join(
                cert_path, "cert.pem"
            )
            key_path = os.path.join(
                cert_path, "key.pem"
            )
        if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
            print(
                "\n"
                "-----------------------------------------\n"
                "---------------->WARNING<----------------\n"
                "The NXBT webapp is being run with self-\n"
                "signed SSL certificates for use on your\n"
                "local network.\n"
                "\n"
                "These certificates ARE NOT safe for\n"
                "production use. Please generate valid\n"
                "SSL certificates if you plan on using the\n"
                "NXBT webapp anywhere other than your own\n"
                "network.\n"
                "-----------------------------------------\n"
                "\n"
                "The above warning will only be shown once\n"
                "on certificate generation."
                "\n"
            )
            print("Generating certificates...")
            cert, key = generate_cert(gethostname())
            with open(cert_path, "wb") as f:
                f.write(cert)
            with open(key_path, "wb") as f:
                f.write(key)

        eventlet.wsgi.server(eventlet.wrap_ssl(eventlet.listen((ip, port)),
            certfile=cert_path, keyfile=key_path), app)
    else:
        eventlet.wsgi.server(eventlet.listen((ip, port)), app)


if __name__ == "__main__":
    start_web_app()
