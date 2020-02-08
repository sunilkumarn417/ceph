import yaml
import contextlib
import logging
from teuthology.orchestra import run
import os
import pwd
import time

log = logging.getLogger(__name__)

DIR = {"v1": {"script": "rgw/v1/tests/s3/",
              "config": "rgw/v1/tests/s3/yamls"},
       "v2": {"script": "rgw/v2/tests/s3_swift/",
              "config": "rgw/v2/tests/s3_swift/configs"}}

REPO_NAME = 'ceph-qe-scripts'
WIP_BRANCH = None
MASTER_BRANCH = 'master'

__doc__ = """
example:
tasks:
    rgw-system-test:
        test: <test-name>
        script: <script-name>        | default value is <test-name>.py
        test_version: <test-version> | ex: v1 or v2, default value is v2
        clients: <clients list>      | ex: [client.0, client.1]
                                        default value is ['client.0]
        config: <configuration of the test-name> |
            default values is the yaml file config from ceph-qe-scripts
"""


@contextlib.contextmanager
def task(ctx, config):
    """
    Task to run RGW IO's using ceph-QE-scripts repo.
    Args:
        ctx: cluster obj
        config: test data
    """
    log.info('starting rgw-tests')
    log.info('config %s' % config)
    if config is None:
        config = {}
    assert isinstance(config, dict), \
        "task set-repo only supports a dictionary for configuration"
    config_file_name = config['test'] + ".yaml"
    log.info('test_version: %s' % config.get('test_version', 'v2'))
    log.info('test: %s' % config['test'])
    log.info('script: %s' % config.get('script', config['test'] + ".py"))
    test_root_dir = config['test'] + "_%d" % int(time.time())
    test_venv = os.path.join(test_root_dir, "venv")
    script = os.path.join(REPO_NAME,
                          DIR[config.get('test_version', 'v2')]['script'],
                          config.get('script', config['test'] + ".py"))
    config_file = os.path.join(REPO_NAME,
                               DIR[config.get('test_version', 'v2')]['config'],
                               config_file_name)
    log.info('script: %s' % script)
    log.info('config_file: %s' % config_file)
    soot = [test_venv, test_root_dir, 'io_info.yaml', '*.json', 'Download.*',
            'Download', '*.mpFile', 'x*', 'key.*', 'Mp.*', '*.key.*']
    cleanup = lambda x: remote.run(args=[run.Raw('sudo rm -rf %s' % x)])
    log.info('listing all clients: %s' % config.get('clients'))
    for role in config.get('clients', ['client.0']):
        assert isinstance(role, basestring)
        PREFIX = 'client.'
        assert role.startswith(PREFIX)
        id_ = role[len(PREFIX):]
        (remote,) = ctx.cluster.only(role).remotes.iterkeys()
        map(cleanup, soot)
        remote.run(args=['mkdir', test_root_dir])
        log.info('cloning the repo to %s' % remote.hostname)
        remote.run(
            args=[
                'cd',
                '%s' % test_root_dir,
                run.Raw(';'),
                'git',
                'clone',
                'https://github.com/red-hat-storage/ceph-qe-scripts.git',
                '-b',
                '%s' % MASTER_BRANCH if WIP_BRANCH is None else WIP_BRANCH
            ])
        if config.get('config', None) is not None:
            test_config = {'config': config.get('config')}
            log.info('config: %s' % test_config)
            log.info('creating configuration from data: %s' % test_config)
            local_file = os.path.join('/tmp/',
                                      config_file_name +
                                      "_" + str(os.getpid()) +
                                      pwd.getpwuid(os.getuid()).pw_name)
            with open(local_file, 'w') as outfile:
                outfile.write(yaml.dump(test_config, default_flow_style=False))
            out = remote.run(args=[run.Raw('sudo echo $HOME')],
                             wait=False,
                             stdout=run.PIPE)
            out = out.stdout.read().strip()
            conf_file = os.path.join(out, test_root_dir, config_file)
            log.info('local_file: %s' % local_file)
            log.info('config_file: %s' % conf_file)
            log.info('copying temp yaml to the client node')
            remote.put_file(local_file, conf_file)
            remote.run(args=['ls', '-lt', os.path.dirname(conf_file)])
            remote.run(args=['cat', conf_file])
            os.remove(local_file)
        remote.run(args=['python3', '-m', 'venv', test_venv])
        remote.run(
            args=[
                'source',
                '{}/bin/activate'.format(test_venv),
                run.Raw(';'),
                run.Raw('pip3 install boto boto3 names PyYaml ConfigParser'),
                run.Raw(';'),
                'deactivate'])

        time.sleep(60)
        log.info('trying to restart rgw service after sleep 60 secs')
        out = remote.run(args=[run.Raw('sudo systemctl is-active ceph-radosgw.target')],
                         wait=False,
                         stdout=run.PIPE)
        try:
            out = out.stdout.read().strip()
        except AttributeError:
            out = "inactive"
        if "inactive" in out:
            log.info('Restarting RGW service')
            remote.run(args=[run.Raw('sudo systemctl restart ceph-radosgw.target')])
        log.info('starting the tests after sleep of 60 secs')
        time.sleep(60)
        remote.run(
            args=[run.Raw(
                'sudo cd %s ' % test_root_dir)])
        remote.run(args=[
            run.Raw('cd %s; sudo venv/bin/python3 %s -c %s ' % (test_root_dir,
                                                                script,
                                                                config_file))])
    try:
        yield
    finally:
        for role in config.get('clients', ['client.0']):
            (remote,) = ctx.cluster.only(role).remotes.iterkeys()
            log.info('Test completed')
            log.info("Deleting leftovers")
            map(cleanup, soot)
