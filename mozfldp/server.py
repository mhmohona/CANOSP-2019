# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from flask import Flask
from flask import request
from flask import jsonify
import numpy as np
import argparse
import json
from decouple import config

HOSTNAME = config("FLDP_HOST", "127.0.0.1")
PORT = config("FLDP_PORT", 8000)

app = Flask(__name__)


class ServerFacade:
    """
    This class acts as a facade around the functions in
    `simulation_util` to provide a consistent interface to load data
    into a classifier.

    Args:
        coef: initial coefficients to initialize the server with
        intercept: initial intercepts to initialize the server with
    """

    def __init__(self, coef, intercept):
        self._coef = coef
        self._intercept = intercept

        self.reset_client_data()

    def reset_client_data(self):
        self._client_coefs = []
        self._client_intercepts = []
        self._num_samples = []

    def ingest_client_data(self, client_json):
        """
        Accepts new weights from a client and stores them on the server side for averaging

        Args:
            client_json: a json object containing coefs, intercepts, and num_samples
        """
        client_json = json.loads(client_json)
        self._client_coefs.append(client_json["coefs"])
        self._client_intercepts.append(client_json["intercept"])
        self._num_samples.append(client_json["num_samples"])

    def compute_new_weights(self):
        """
        Applies the federated averaging on the stored client weights for this round
        and return the new weights
        """

        new_coefs = np.zeros(self._coef.shape, dtype=np.float64, order="C")
        new_intercept = np.zeros(self._intercept.shape, dtype=np.float64, order="C")

        total_samples = sum(self._num_samples)

        for index, (client_coef, client_intercept, n_k) in enumerate(
            zip(self._client_coefs, self._client_intercepts, self._num_samples)
        ):
            added_coef = np.array(client_coef) * n_k / total_samples
            added_intercept = np.array(client_intercept) * n_k / total_samples

            new_coefs = np.add(new_coefs, added_coef)
            new_intercept = np.add(new_intercept, added_intercept)

        # update the server weights to newly calculated weights
        self._coef = new_coefs
        self._intercept = new_intercept

        # reset all client data so it doesn't get used for the next round
        self.reset_client_data()

        return self._coef, self._intercept

    def compute_new_weights_dp(self, standard_dev, avg_denom):
        """
        Applies the DP-protected federated averaging on the stored client weights
        for this round and return the new weights.

        standard_dev: the standard deviation of the random noise to apply
        avg_denom: the denominator to use in computing the average
        """
        # TODO: DP version of fed averaging.
        pass


class InvalidClientData(Exception):
    status_code = 500

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv["message"] = self.message
        return rv


@app.errorhandler(InvalidClientData)
def handle_invalid_client_data(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.route("/api/v1/ingest_client_data/<string:client_id>", methods=["POST"])
def ingest_client_data(client_id):
    payload = request.get_json()
    try:
        app.facade.ingest_client_data(payload)
        return {"result": "ok"}
    except Exception as exc:
        raise InvalidClientData(
            "Error updating client", payload={"exception": str(exc)}
        )


@app.route("/api/v1/compute_new_weights", methods=["POST"])
def compute_new_weights():
    try:
        weights = app.facade.compute_new_weights()
        json_safe_weights = [w.tolist() for w in weights]
        return {"result": "ok", "weights": json_safe_weights}
    except Exception as exc:
        raise InvalidClientData(
            "Error computing weights", payload={"exception": str(exc)}
        )


def flaskrun(app, default_host=HOSTNAME, default_port=PORT):
    """
    Takes a flask.Flask instance and runs it. Parses
    command-line flags to configure the app.
    """

    # Set up the command-line options
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-H",
        "--host",
        help="Hostname of the Flask app " + "[default %s]" % default_host,
        default=default_host,
    )
    parser.add_argument(
        "-P",
        "--port",
        help="Port for the Flask app " + "[default %s]" % default_port,
        default=default_port,
    )

    # Two options useful for debugging purposes, but
    # a bit dangerous so not exposed in the help message.
    parser.add_argument("-d", "--debug", action="store_true", dest="debug")
    parser.add_argument("-p", "--profile", action="store_true", dest="profile")

    args = parser.parse_args()

    # If the user selects the profiling option, then we need
    # to do a little extra setup
    if args.profile:
        from werkzeug.contrib.profiler import ProfilerMiddleware

        app.config["PROFILE"] = True
        app.wsgi_app = ProfilerMiddleware(app.wsgi_app, restrictions=[30])
        args.debug = True

    NUM_LABELS = 10
    NUM_FEATURES = 784
    coef = np.zeros((NUM_LABELS, NUM_FEATURES), dtype=np.float64, order="C")
    intercept = np.zeros(NUM_LABELS, dtype=np.float64, order="C")

    app.facade = ServerFacade(coef, intercept)

    app.run(debug=args.debug, host=args.host, port=int(args.port))


if __name__ == "__main__":
    with app.app_context():
        flaskrun(app)
