from __future__ import print_function
import os, sys
sys.path.append(os.getcwd())

import random
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import sklearn.datasets

from util import argprun


def run(mode="wgan-gp", dataset='8gaussians', dim=512,
        critic_iters=5, fixed_generator=False,
        batch_size=256, iters=100000, penalty_weight=0.1,
        one_sided=True, penalty_mode="grad", log_dir="toy_log_default", gpu=0):       # grad or pagan or ot

    loca = locals().copy()

    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
    os.environ["CUDA_VISIBLE_DEVICES"] = "{}".format(gpu)

    from tensorflow.python.client import device_lib
    print("VISIBLE DEVICES {}".format(str(device_lib.list_local_devices())))

    print("\n\n\n")

    import tensorflow as tf
    import tflib as lib
    import tflib.ops.linear
    import tflib.plot

    if os.path.exists(log_dir):
        raise Exception("log_dir {} exists".format(log_dir))
    else:
        os.makedirs(log_dir)

    lib.plot.logdir = log_dir
    print("log dir set to {}".format(lib.plot.logdir))

    with open("{}/settings.dict".format(log_dir), "w") as f:
        if penalty_mode != "grad":
            del loca["one_sided"]
        f.write(str(loca))
        print("saved settings: {}".format(loca))

    MODE = mode # wgan or wgan-gp
    DATASET = dataset # 8gaussians, 25gaussians, swissroll
    DIM = dim # Model dimensionality
    FIXED_GENERATOR = fixed_generator # whether to hold the generator fixed at real data plus
                            # Gaussian noise, as in the plots in the paper
    LAMBDA = penalty_weight # Smaller lambda makes things faster for toy tasks, but isn't
                # necessary if you increase CRITIC_ITERS enough
    CRITIC_ITERS = critic_iters # How many critic iterations per generator iteration
    BATCH_SIZE = batch_size # Batch size
    ITERS = iters # how many generator iterations to train for

    ONE_SIDED = one_sided

    lib.print_model_settings(loca)

    def ReLULayer(name, n_in, n_out, inputs):
        output = lib.ops.linear.Linear(
            name+'.Linear',
            n_in,
            n_out,
            inputs,
            initialization='he'
        )
        output = tf.nn.relu(output)
        return output

    def Generator(n_samples, real_data):
        if FIXED_GENERATOR:
            return real_data + (1.*tf.random_normal(tf.shape(real_data)))
        else:
            noise = tf.random_normal([n_samples, 2])
            output = ReLULayer('Generator.1', 2, DIM, noise)
            output = ReLULayer('Generator.2', DIM, DIM, output)
            output = ReLULayer('Generator.3', DIM, DIM, output)
            output = lib.ops.linear.Linear('Generator.4', DIM, 2, output)
            return output

    def Discriminator(inputs):
        output = ReLULayer('Discriminator.1', 2, DIM, inputs)
        output = ReLULayer('Discriminator.2', DIM, DIM, output)
        output = ReLULayer('Discriminator.3', DIM, DIM, output)
        output = lib.ops.linear.Linear('Discriminator.4', DIM, 1, output)
        return tf.reshape(output, [-1])

    real_data = tf.placeholder(tf.float32, shape=[None, 2])
    fake_data = Generator(BATCH_SIZE, real_data)

    disc_real = Discriminator(real_data)
    disc_fake = Discriminator(fake_data)

    # WGAN loss
    disc_cost = tf.reduce_mean(disc_fake) - tf.reduce_mean(disc_real)
    gen_cost = -tf.reduce_mean(disc_fake)

    # WGAN gradient penalty
    if MODE == 'wgan-gp':
        alpha = tf.random_uniform(
            shape=[BATCH_SIZE,1],
            minval=0.,
            maxval=1.
        )
        interpolates = alpha*real_data + ((1-alpha)*fake_data)
        disc_interpolates = Discriminator(interpolates)
        gradients = tf.gradients(disc_interpolates, [interpolates])[0]
        slopes = tf.sqrt(tf.reduce_sum(tf.square(gradients), reduction_indices=[1]))
        if penalty_mode == "grad":
            if not ONE_SIDED:
                gradient_penalty = tf.reduce_mean((slopes-1.)**2)
            else:
                gradient_penalty = tf.reduce_mean(tf.clip_by_value(slopes - 1., 0., np.infty)**2)
        elif penalty_mode == "pagan" or penalty_mode == "ot":
            EPS = 1e-6
            print("SHAPES OF REAL AND FAKE DATA FOR PAGAN AND OT: ", real_data.get_shape(), fake_data.get_shape())
            print("TYPES: ", type(real_data), type(fake_data))
            interp_real = real_data
            interp_fake = fake_data
            _score_real = Discriminator(interp_real)
            _score_fake = Discriminator(interp_fake)
            real_fake_dist = tf.norm(interp_real - interp_fake, ord=2, axis=1)
            print("SCORES AND DIST SHAPES: ", _score_real.get_shape(), _score_fake.get_shape(), real_fake_dist.get_shape())
            print("TYPES: ", type(_score_real), type(_score_fake), type(real_fake_dist))

            if penalty_mode == "pagan":
                penalty_vecs = tf.clip_by_value(
                        (tf.abs(_score_real - _score_fake)
                         / tf.clip_by_value(real_fake_dist, EPS, np.infty))
                        - 1,
                    0, np.infty) ** 2
            elif penalty_mode == "ot":
                penalty_vecs = tf.clip_by_value(
                        _score_real - _score_fake - real_fake_dist,
                    0, np.infty) ** 2
            print("PENALTY VEC SHAPE: ", penalty_vecs.get_shape())
            gradient_penalty = tf.reduce_mean(penalty_vecs)
        else:
            raise Exception("unknown penalty mode {}".format(penalty_mode))
        disc_cost += LAMBDA*gradient_penalty

    disc_params = lib.params_with_name('Discriminator')
    gen_params = lib.params_with_name('Generator')

    if MODE == 'wgan-gp':
        disc_train_op = tf.train.AdamOptimizer(
            learning_rate=1e-4,
            beta1=0.5,
            beta2=0.9
        ).minimize(
            disc_cost,
            var_list=disc_params
        )
        if len(gen_params) > 0:
            gen_train_op = tf.train.AdamOptimizer(
                learning_rate=1e-4,
                beta1=0.5,
                beta2=0.9
            ).minimize(
                gen_cost,
                var_list=gen_params
            )
        else:
            gen_train_op = tf.no_op()

    else:
        disc_train_op = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(
            disc_cost,
            var_list=disc_params
        )
        if len(gen_params) > 0:
            gen_train_op = tf.train.RMSPropOptimizer(learning_rate=5e-5).minimize(
                gen_cost,
                var_list=gen_params
            )
        else:
            gen_train_op = tf.no_op()


        # Build an op to do the weight clipping
        clip_ops = []
        for var in disc_params:
            clip_bounds = [-.01, .01]
            clip_ops.append(
                tf.assign(
                    var,
                    tf.clip_by_value(var, clip_bounds[0], clip_bounds[1])
                )
            )
        clip_disc_weights = tf.group(*clip_ops)

    print("Generator params:")
    for var in lib.params_with_name('Generator'):
        print("\t{}\t{}".format(var.name, var.get_shape()))
    print("Discriminator params:")
    for var in lib.params_with_name('Discriminator'):
        print("\t{}\t{}".format(var.name, var.get_shape()))

    frame_index = [0]
    def generate_image(true_dist, saveidx=None):
        """
        Generates and saves a plot of the true distribution, the generator, and the
        critic.
        """
        N_POINTS = 128
        RANGE = 3

        points = np.zeros((N_POINTS, N_POINTS, 2), dtype='float32')
        points[:,:,0] = np.linspace(-RANGE, RANGE, N_POINTS)[:,None]
        points[:,:,1] = np.linspace(-RANGE, RANGE, N_POINTS)[None,:]
        points = points.reshape((-1,2))
        samples, disc_map = session.run(
            [fake_data, disc_real],
            feed_dict={real_data:points}
        )
        disc_map = session.run(disc_real, feed_dict={real_data:points})

        plt.clf()

        fig = plt.gcf()
        fig.set_size_inches(7, 7)

        axes = plt.gca()
        axes.set_xlim([-2, 2])
        axes.set_ylim([-2, 2])

        x = y = np.linspace(-RANGE, RANGE, N_POINTS)
        plt.contour(x,y,disc_map.reshape((len(x), len(y))).transpose())

        plt.scatter(true_dist[:, 0], true_dist[:, 1], c='orange',  marker='+')
        plt.scatter(samples[:, 0],    samples[:, 1],    c='green', marker='+')

        saveidx = frame_index[0] if saveidx is None else saveidx

        plt.savefig('{}/frame_{}.pdf'.format(log_dir, saveidx), format="pdf")
        frame_index[0] += 1

    # Dataset iterator
    def inf_train_gen():
        if DATASET == '25gaussians':

            dataset = []
            for i in xrange(100000/25):
                for x in xrange(-2, 3):
                    for y in xrange(-2, 3):
                        point = np.random.randn(2)*0.05
                        point[0] += 2*x
                        point[1] += 2*y
                        dataset.append(point)
            dataset = np.array(dataset, dtype='float32')
            np.random.shuffle(dataset)
            dataset /= 2.828 # stdev
            while True:
                for i in xrange(len(dataset)/BATCH_SIZE):
                    yield dataset[i*BATCH_SIZE:(i+1)*BATCH_SIZE]

        elif DATASET == 'swissroll':

            while True:
                data = sklearn.datasets.make_swiss_roll(
                    n_samples=BATCH_SIZE,
                    noise=0.25
                )[0]
                data = data.astype('float32')[:, [0, 2]]
                data /= 7.5 # stdev plus a little
                yield data

        elif DATASET == '8gaussians':

            scale = 2.
            centers = [
                (1,0),
                (-1,0),
                (0,1),
                (0,-1),
                (1./np.sqrt(2), 1./np.sqrt(2)),
                (1./np.sqrt(2), -1./np.sqrt(2)),
                (-1./np.sqrt(2), 1./np.sqrt(2)),
                (-1./np.sqrt(2), -1./np.sqrt(2))
            ]
            centers = [(scale*x,scale*y) for x,y in centers]
            while True:
                dataset = []
                for i in xrange(BATCH_SIZE):
                    point = np.random.randn(2)*.02
                    center = random.choice(centers)
                    point[0] += center[0]
                    point[1] += center[1]
                    dataset.append(point)
                dataset = np.array(dataset, dtype='float32')
                dataset /= 1.414 # stdev
                yield dataset

    # Train loop!
    config = tf.ConfigProto(allow_soft_placement=True)
    config.gpu_options.allow_growth = True

    with tf.Session(config=config) as session:
        session.run(tf.initialize_all_variables())
        gen = inf_train_gen()
        for iteration in xrange(ITERS):
            # Train generator
            if iteration > 0:
                _ = session.run(gen_train_op)
            # Train critic
            for i in xrange(CRITIC_ITERS):
                _data = gen.next()
                _disc_cost, _ = session.run(
                    [disc_cost, disc_train_op],
                    feed_dict={real_data: _data}
                )
                if MODE == 'wgan':
                    _ = session.run([clip_disc_weights])
            # Write logs and save samples
            lib.plot.plot('disc cost', _disc_cost)
            if iteration % 100 == 99 or iteration == 10 or iteration == 50:
                lib.plot.flush()
                generate_image(_data, saveidx=iteration)
            lib.plot.tick()


if __name__ == "__main__":
    argprun(run)
