import multiprocessing as mp
import os
import random
import threading
import uuid

import flask
from flask import json
from flask import views
import flask_socketio as io
import pyspark
from pyspark import sql as pysql
from pyspark.sql import functions as pyfuncs


class HTMLContent(views.MethodView):
    def get(self):
        return flask.render_template('index.html')


class PredictionAPI(views.MethodView):
    def __init__(self, input_queue):
        super(PredictionAPI, self).__init__()
        self.input_queue = input_queue

    def post(self):
        data = flask.request.json
        data.update({
            'status': 'training',
            'id': uuid.uuid4().hex})
        self.input_queue.put(data)
        return json.jsonify(data)


def portfolio_value(pf):
    return sum([v for v in pf.values()])


def seeds(count):
    return [random.randint(0, 1 << 32 - 1) for i in range(count)]


def simstep(pf, params, prng):
    def daily_return(sym):
        mean, stddev = params[sym]
        change = (prng.normalvariate(mean, stddev) + 100) / 100.0
        return change
    return {s: daily_return(s) * v for s, v in pf.items()}


def simulate(seed, pf, params, days):
    prng = random.Random()
    prng.seed(seed)
    pf = pf.copy()
    for day in range(days):
        pf = simstep(pf, params, prng)
    return pf


def processing_loop(spark_master, input_queue, output_queue, wikieod_file):
    sconf = pyspark.SparkConf()
    sconf.setAppName('var-sandbox').setMaster(spark_master)
    sc = pyspark.SparkContext(conf=sconf)

    output_queue.put('ready')

    spark = pysql.SparkSession.builder.master(spark_master).getOrCreate()
    df = spark.read.load(wikieod_file)
    ddf = df.select('ticker', 'date', 'close').withColumn(
        'change', (pyfuncs.col('close') / pyfuncs.lag('close', 1).over(
        pysql.Window.partitionBy('ticker').orderBy(
        df['date'])) - 1.0) * 100)

    mv = ddf.groupBy('ticker').agg(pyfuncs.avg('change').alias('mean'),
        pyfuncs.sqrt(pyfuncs.variance('change')).alias('stddev'))

    dist_map = mv.rdd.map(lambda r: (r[0], (r[1], r[2]))).collectAsMap()

    priceDF = ddf.orderBy('date', ascending=False).groupBy('ticker').agg(
        pyfuncs.first('close').alias('price'),
        pyfuncs.first('date').alias('date'))
    prices = priceDF.rdd.map(lambda r: (r[0], r[1])).collectAsMap()

    while True:
        req = input_queue.get()
        portfolio = {}
        for stock in req['stocks']:
            portfolio[stock['symbol']] = (
                prices[stock['symbol']] * stock['quantity'])

        seed_rdd = sc.parallelize(seeds(10000))
        bparams = sc.broadcast(dist_map)
        bpf = sc.broadcast(portfolio)
        initial_value = portfolio_value(portfolio)
        results = seed_rdd.map(lambda s:
            portfolio_value(simulate(s, bpf.value, bparams.value, req['days']))
            - initial_value)
        simulated_results = list(zip(results.collect(), seed_rdd.collect()))
        simulated_values = [v for (v, _) in simulated_results]
        simulated_values.sort()
        num_samples = req['simulations'] if req['simulations'] < 100 else 100
        prediction = [
            simulated_values[int(len(simulated_values) * i / num_samples)]
            for i in range(num_samples)]
        req.update({
            'status': 'ready',
            'prediction': prediction})
        output_queue.put(req)


def responder_loop(socketio, output_queue):
    while True:
        res = output_queue.get()
        socketio.emit('update', json.dumps(res))


def main():
    spark_master = os.environ.get('SPARK_MASTER', 'local[*]')
    wikieod_file = os.environ.get('WIKIEOD_FILE')
    input_queue = mp.Queue()
    output_queue = mp.Queue()

    process = mp.Process(target=processing_loop,
        args=(spark_master, input_queue, output_queue, wikieod_file))
    process.start()

    output_queue.get()

    app = flask.Flask(__name__)
    app.config['SECRET_KEY'] = 'secret!'
    app.add_url_rule('/', view_func=HTMLContent.as_view('html'))
    app.add_url_rule('/predictions',
                     view_func=PredictionAPI.as_view('predictions',
                                                     input_queue))

    socketio = io.SocketIO(app)
    thread = threading.Thread(
        target=responder_loop, args=(socketio, output_queue))
    thread.start()
    socketio.run(app, host='0.0.0.0', port=8080)


if __name__ == '__main__':
    main()
