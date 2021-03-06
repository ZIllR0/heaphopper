#!/usr/bin/env python

import os
import re
import time
import datetime
from subprocess import check_call, check_output, STDOUT, CalledProcessError, PIPE, DEVNULL
import glob
import nose
from flaky import flaky
import sys
import logging

logger = logging.getLogger('heaphopper-test')

VERBOSE = False
OK = "OK"
ERROR = "ERROR"

BASE_DIR = os.path.dirname(os.path.realpath(__file__))


def store_results(results_dict):
    ts = time.time()
    dt = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d_%H:%M:%S')
    fn = '{}_test.txt'.format(dt)

    total_time = 0
    with open(fn, 'w') as f:
        f.write('Timing results for test run from {}\n\n'.format(dt))
        for dir in results_dict.keys():
            f.write('[{}]{}: {} s\n'.format('OK' if results_dict[dir]['worked'] else 'FAILED', dir,
                                            results_dict[dir]['ts']))
            total_time += results_dict[dir]['ts']
        f.write('total time: {} s\n'.format(total_time))


def run_single(folder_name, analysis_name, binary_name):
    status = OK
    start = time.time()
    try:
        cmd = ['python', 'heaphopper.py', 'trace', '-c', '{}/{}'.format(folder_name, analysis_name), '-b',
             '{}/{}'.format(folder_name, binary_name)]
        output = check_output(cmd, cwd='{}/../'.format(BASE_DIR), stderr=STDOUT)
    except CalledProcessError as e:
        if e.output:
            logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
            logger.error(e.output.decode('utf-8'))
        status = ERROR
    nose.tools.assert_equal(status, OK, "The symbolic execution failed with an non-zero exit code.")
    ts = time.time() - start
    return ts, output


def check_single(result_path, folder_name, analysis_name, binary_name):
    status = OK
    ts, output = run_single(folder_name, analysis_name, binary_name)
    if not os.path.isfile(result_path):
        logger.error("Error tracing {}. Log-output:".format(analysis_name))
        logger.error(output.decode('utf-8'))
        status = ERROR
    msg = "Couldn't find result files: This indicates a problem with the sybmolic execution in angr and means we " \
          "failed to reach expected bad-state. "
    nose.tools.assert_equal(status, OK, msg)
    return ts


def create_poc_single(folder_name, analysis_name, binary_name, result_name, desc_name, source_name, poc_path):
    status = OK
    if not VERBOSE:
        try:
            cmd = ['python', 'heaphopper.py', 'poc',
                                   '-c', '{}/{}'.format(folder_name, analysis_name),
                                   '-b', '{}/{}'.format(folder_name, binary_name),
                                   '-r', '{}'.format(result_name),
                                   '-d', '{}'.format(desc_name),
                                   '-s', '{}'.format(source_name)]
            output = check_output(cmd, cwd='{}/../'.format(BASE_DIR), stderr=STDOUT)
        except CalledProcessError as e:
            if e.output:
                logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
                logger.error(e.output.decode('utf-8'))
            status = ERROR

        msg = "Failed to merge the placeholder source code and the symbolic state into a concrete source code.\nThis " \
              "indicates an problem with heaphopper internals and could be a bug or a problem with recent changes in " \
              "angr. "

        nose.tools.assert_equal(status, OK, msg)

        poc_path = glob.glob(poc_path)[0]
        try:
            cmd = ['make', '-C', poc_path, 'pocs-print']
            output = check_output(cmd, stderr=STDOUT)
        except CalledProcessError as e:
            if e.output:
                logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
                logger.error(e.output.decode('utf-8'))
            status = ERROR

        msg = "Failed to compile the synthesized concrete source code.\nMost likely the poc-generation created invalid " \
              "C. This is a strong indication for ab bug in the poc-generation and most likely has nothing to do with " \
              "the symbolic execution in angr. "
        nose.tools.assert_equal(status, OK, msg)

    else:
        try:
            cmd = ['python', 'heaphopper.py', 'poc',
                                      '-c', '{}/{}'.format(folder_name, analysis_name),
                                      '-b', '{}/{}'.format(folder_name, binary_name),
                                      '-r', '{}'.format(result_name),
                                      '-d', '{}'.format(desc_name),
                                      '-s', '{}'.format(source_name)]
            logger.info(check_output(cmd, cwd='{}/../'.format(BASE_DIR)), stderr=STDOUT)
        except CalledProcessError as e:
            if e.output:
                logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
                logger.error(e.output.decode('utf-8'))
            status = ERROR

        msg = "Failed to merge the placeholder source code and the symbolic state into a concrete source code. This " \
              "indicates an problem with heaphopper internals and could be a bug or a problem with changes in angr. "
        nose.tools.assert_equal(status, OK, msg)

        poc_path = glob.glob(poc_path)[0]

        try:
            cmd = ['make', '-C', poc_path, 'pocs-print']
            logger.info(check_output(cmd))
        except CalledProcessError as e:
            if e.output:
                logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
                logger.error(e.output.decode('utf-8'))
            status = ERROR
        msg = "Failed to compile the synthesized concrete source code. Most likely the poc-generation created invalid " \
              "C. This is a strong indication for ab bug in the poc-generation and most likely has nothing to do with " \
              "the symbolic execution in angr. "
        nose.tools.assert_equal(status, OK, msg)

    return True


def verify_poc_single(poc_path, poc_type):
    status = OK
    poc_path = glob.glob(poc_path)[0]
    poc_bin = '{}/bin/poc_0_0.bin'.format(poc_path)

    try:
        cmd = ['./ld-linux-x86-64.so.2', poc_bin]
        output = check_output(cmd,
                              env={"LD_PRELOAD": "./libc.so.6", "LIBC_FATAL_STDERR_": "1"}, cwd='{}'.format(BASE_DIR),
                              stderr=STDOUT)
    except CalledProcessError as e:
        logger.error("CalledProcessError: Traceback of running {}:".format(cmd))
        logger.error(e.output.decode('utf-8'))
        status = ERROR

    msg = "Running the POC failed with an non-zero exit code. This is a strong indication for a bug in the " \
          "poc-generation and most likely has nothing to do with the symbolic execution in angr. "
    nose.tools.assert_equal(status, OK, msg)

    if VERBOSE:
        logger.info(output)

    if poc_type == 'malloc_non_heap':
        res = verify_non_heap(output)
        if not res:
            logger.error("Error running POC {}. output:".format(poc_bin))
            logger.error(output.decode('utf-8'))
            status = ERROR
        msg = "The concrete execution did not reach the malloc_non_heap state. This is a strong indication for a bug " \
              "in the poc-generation and most likely has nothing to do with the symbolic execution in angr. "
        nose.tools.assert_equal(status, OK, msg)
        return res

    elif poc_type == 'malloc_allocated':
        res = verify_malloc_allocated(output)
        if not res:
            logger.error("Error running POC {}. output:".format(poc_bin))
            logger.error(output.decode('utf-8'))
            status = ERROR
        msg = "The concrete execution did not reach the malloc_allocated state. This is a strong indication for a bug " \
              "in the poc-generation and most likely has nothing to do with the symbolic execution in angr. "
        nose.tools.assert_equal(status, OK, msg)
        return res
    elif poc_type.startswith('arbitrary_write'):
        res = verify_arbitrary_write(output)
        if not res:
            logger.error("Error running POC {}. output:".format(poc_bin))
            logger.error(output.decode('utf-8'))
            status = ERROR
        msg = "The concrete execution did not trigger an arbitrary write. This is a strong indication for a bug in " \
              "the poc-generation and most likely has nothing to do with the symbolic execution in angr. "
        nose.tools.assert_equal(status, OK, msg)
        return res


def verify_non_heap(output):
    heap_base = int(re.findall(b"Init printf: ([0-9a-fx]+)", output)[0], 0)
    last_alloc = int(re.findall(b"Allocation: ([0-9a-fx]+)", output)[-1], 0)
    if last_alloc < heap_base:
        return True
    return False


def verify_malloc_allocated(output):
    allocs = [(int(f[0], 16), int(f[1], 16)) for f in re.findall(b"Allocation: ([0-9a-fx]+)\nSize: ([0-9a-fx]+)",
                                                                 output)]
    for i, (a1, s1) in enumerate(allocs):
        for a2, s2 in allocs[i + 1:]:
            if a1 == a2:
                return True
            if a1 < a2 < a1 + s1:
                return True
            if a2 < a1 < a2 + s2:
                return True

    return False


def verify_arbitrary_write(output):
    pre = dict()
    for (i, a) in re.findall(b"write_target\[([0-9]+)\]: ([0-9a-fx]+|\(nil\))\n", output):
        if a == b'(nil)':
            a = '0x0'

        if i not in pre:
            pre[i] = int(a, 0)
        else:
            if pre[i] != int(a, 0):
                return True

    return False


def test_01_make():
    output = check_output(['make', '-C', BASE_DIR, 'clean'])
    if VERBOSE:
        logger.info(output)
    output = check_output(['make', '-C', BASE_DIR])
    if VERBOSE:
        logger.info(output)


@flaky(max_runs=3, min_passes=1)
def test_02_fastbin_dup():
    TIME = 25
    info = dict(folder_name='how2heap_fastbin_dup', conf='analysis.yaml', bin_name='fastbin_dup.bin',
                type='malloc_non_heap')
    location = str(os.path.join(BASE_DIR, info['folder_name']))

    result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
    desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
    source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
    poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])

    ts = check_single(result_path, location, info['conf'], info['bin_name'])
    # nose.tools.assert_less(ts, TIME)

    created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path,
                                    poc_path)
    nose.tools.assert_true(created_poc)

    poc_worked = verify_poc_single(poc_path, info['type'])
    nose.tools.assert_true(poc_worked)


@flaky(max_runs=3, min_passes=1)
def test_03_house_of_lore():
    TIME = 90
    info = dict(folder_name='how2heap_house_of_lore', conf='analysis.yaml', bin_name='house_of_lore.bin',
                type='malloc_non_heap')
    location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
    result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
    desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
    source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
    poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])

    ts = check_single(result_path, location, info['conf'], info['bin_name'])
    # nose.tools.assert_less(ts, TIME)

    exists = os.path.isfile(result_path) and os.path.isfile(desc_path)
    nose.tools.assert_true(exists)

    created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path,
                                    poc_path)
    nose.tools.assert_true(created_poc)

    poc_worked = verify_poc_single(poc_path, info['type'])
    nose.tools.assert_true(poc_worked)


def test_04_house_of_spirit():
    TIME = 30
    info = dict(folder_name='how2heap_house_of_spirit', conf='analysis.yaml', bin_name='house_of_spirit.bin',
                type='malloc_non_heap')
    location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
    result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
    desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
    source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
    poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])

    ts = check_single(result_path, location, info['conf'], info['bin_name'])
    # nose.tools.assert_less(ts, TIME)

    created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path,
                                    poc_path)
    nose.tools.assert_true(created_poc)

    poc_worked = verify_poc_single(poc_path, info['type'])
    nose.tools.assert_true(poc_worked)


def test_05_overlapping_chunks():
    TIME = 30
    info = dict(folder_name='how2heap_overlapping_chunks', conf='analysis.yaml', bin_name='overlapping_chunks.bin',
                type='malloc_allocated')
    location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
    result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
    desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
    source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
    poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])

    ts = check_single(result_path, location, info['conf'], info['bin_name'])
    # nose.tools.assert_less(ts, TIME)

    created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path,
                                    poc_path)
    nose.tools.assert_true(created_poc)

    poc_worked = verify_poc_single(poc_path, info['type'])
    nose.tools.assert_true(poc_worked)


def test_06_unsorted_bin_attack():
    TIME = 20
    info = dict(folder_name='how2heap_unsorted_bin_attack', conf='analysis.yaml', bin_name='unsorted_bin_attack.bin',
                type='arbitrary_write_malloc')
    location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
    result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
    desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
    source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
    poc_path = '{}/pocs/{}/*/{}'.format(location, info['type'], info['bin_name'])

    ts = check_single(result_path, location, info['conf'], info['bin_name'])
    # nose.tools.assert_less(ts, TIME)

    created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path,
                                    poc_path)
    nose.tools.assert_true(created_poc)

    poc_worked = verify_poc_single(poc_path, info['type'])
    nose.tools.assert_true(poc_worked)


# def test_07_unsafe_unlink():
#        TIME=500
#        info = dict(folder_name='how2heap_unsafe_unlink', conf='analysis.yaml', bin_name='unsafe_unlink.bin', type='arbitrary_write_free')
#        location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
#        result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
#        desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
#        source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
#        poc_path = '{}/pocs/{}/*/{}'.format(location, info['type'], info['bin_name'])
#
#        ts = check_single(result_path, location, info['conf'], info['bin_name'])
#        #nose.tools.assert_less(ts, TIME)
#
#        created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path, poc_path)
#        nose.tools.assert_true(created_poc)
#
#        poc_worked = verify_poc_single(poc_path, info['type'])
#        nose.tools.assert_true(poc_worked)
#
# def test_08_house_of_einherjar():
#        TIME=500
#        info = dict(folder_name='how2heap_house_of_einherjar', conf='analysis.yaml', bin_name='house_of_einherjar.bin', type='malloc_non_heap')
#        location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
#        result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
#        desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
#        source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
#        poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])
#
#        ts = check_single(result_path, location, info['conf'], info['bin_name'])
#        #nose.tools.assert_less(ts, TIME)

#        exists = os.path.isfile(result_path) and os.path.isfile(desc_path)
#        nose.tools.assert_true(exists)
#
#        created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path, poc_path)
#        nose.tools.assert_true(created_poc)
#
#        poc_worked = verify_poc_single(poc_path, info['type'])
#        nose.tools.assert_true(poc_worked)

def test_09_poison_null_byte():
        TIME=500
        info = dict(folder_name='how2heap_poison_null_byte', conf='analysis.yaml', bin_name='poison_null_byte.bin', type='malloc_allocated')
        location = str(os.path.join(os.path.dirname(os.path.realpath(__file__)), info['folder_name']))
        result_path = '{}/{}-result.yaml'.format(location, info['bin_name'])
        desc_path = '{}/{}-desc.yaml'.format(location, info['bin_name'])
        source_path = '{}/{}.c'.format(location, info['bin_name'].split('.')[0])
        poc_path = '{}/pocs/{}/{}'.format(location, info['type'], info['bin_name'])

        ts = run_single(location, info['conf'], info['bin_name'])
        #nose.tools.assert_less(ts, TIME)

        exists = os.path.isfile(result_path) and os.path.isfile(desc_path)
        nose.tools.assert_true(exists)

        created_poc = create_poc_single(location, info['conf'], info['bin_name'], result_path, desc_path, source_path, poc_path)
        nose.tools.assert_true(created_poc)

        poc_worked = verify_poc_single(poc_path, info['type'])
        nose.tools.assert_true(poc_worked)


def run_all():
    functions = globals()
    all_functions = dict(list(filter((lambda k_v: k_v[0].startswith('test_')), list(functions.items()))))
    for f in sorted(all_functions.keys()):
        if hasattr(all_functions[f], '__call__'):
            all_functions[f]()


if __name__ == "__main__":

    # Run tests
    if len(sys.argv) > 1:
        globals()['test_' + sys.argv[1]]()
    else:
        run_all()
