import os
import sys
import logging
import tempfile
import subprocess
from datetime import datetime
from mozprocess import ProcessHandler

from bisect_b2g.util import run_cmd


GOOD = 69
BAD = 96

log = logging.getLogger(__name__)


class EvaluatorError(Exception):
    pass


class Evaluator(object):

    def __init__(self):
        object.__init__(self)

    def eval(self, history_line):
        assert 0, "Unimplemented"


class ScriptEvaluator(Evaluator):

    def __init__(self, script):
        Evaluator.__init__(self)
        self.script = script

    def eval(self, history_line):
        log.debug("Running script evaluator with %s", self.script)
        code, output = run_cmd(command=self.script, rc_only=True)
        log.debug("Script evaluator returned %d", code)
        return code == 0


class InteractiveEvaluator(Evaluator):

    def __init__(self, stdin_file=sys.stdin):
        Evaluator.__init__(self)
        self.stdin_file = stdin_file

    def generate_script(self):
        rcfile = """
        echo
        echo "To mark a changeset, type either 'good' or 'bad'"
        echo

        function good () {
            exit %d
        }

        function bad () {
            exit %d
        }

        """ % (GOOD, BAD)
        tmpfd, tmpn = tempfile.mkstemp()
        os.write(tmpfd, rcfile)
        os.close(tmpfd)
        return tmpn

    def eval(self, history_line):
        # STEPS:
        # 1. create env with PS1
        # 2. create bash script file with good and bad programs
        # 3. start bash using $SHELL and including the BASH_ENV from 2.
        # 4. Return True if RC=69 and False if RC=96
        # Improvments:
        #   * history bash command to show which changesets are dismissed
        rcfile = self.generate_script()
        env = dict(os.environ)
        env['PS1'] = "BISECT: $ "
        env['PS2'] = "> "
        env['IGNOREEOF'] = str(1024*4)

        # We don't use run_cmd here because that function uses
        # subprocess.Popen.communicate, which wait()s for the
        # process before displaying output.  That doesn't work
        # here because we're doing "smart" things here
        code = subprocess.call(
            [os.environ['SHELL'], "--rcfile", rcfile, "--noprofile"],
            env=env, stdout=sys.stdout, stderr=sys.stderr,
            stdin=self.stdin_file)

        if os.path.exists(rcfile):
            os.unlink(rcfile)

        if code == GOOD:
            rv = True
        elif code == BAD:
            rv = False
        elif code == 0:
            log.warning("Received an exit command from interactive " +
                        " console, exiting bisection completely")
            exit(1)
        else:
            raise EvaluatorError(
                "An unexpected exit code '%d' occured in " % code +
                "the interactive prompt")
        log.debug("Interactive evaluator returned %d", code)
        return rv

class InteractiveBuildEvaluator(InteractiveEvaluator):
    def __init__(self, build_info=None, stdin_file=sys.stdin):
        InteractiveEvaluator.__init__(self)
        self.stdin_file = stdin_file
        self.build_number = 0
        self.log_open = False
        if not build_info:
            self.build_info['workdir'] = os.getcwd()
            self.build_info['env'] = os.environ()
            self.build_info['logdir'] = os.getcwd()
        else:
            self.build_info = build_info
            if self.build_info['env']:
                edict = {}
                elist = self.build_info['env'].split(',')
                for p in elist:
                    edict[p.split('=')[0]] = p.split('=')[1]
                self.build_info['env'] = edict

    def perform_build(self, history_line):
        import pdb
        pdb.set_trace()
        self.build_number += 1
        self.start_time = datetime.now()
        log.debug("Performing build %d on history line: %s" % (self.build_number, history_line))
        build_proc = ProcessHandler(cmd = ['/home/ctalbert/projects/b2g-hamachi/build.sh'],
                                    cwd = self.build_info['workdir'],
                                    env=self.build_info['env'],
                                    processOutputLine=[self.notify_status],
                                    kill_on_timeout=True,
                                    onTimeout=[self.notify_timeout],
                                    onFinish=[self.notify_finished],
                                    shell=True)

        try:
            sys.stdout.write("Starting Build %d:" % self.build_number)
            build_proc.run(timeout=7200)
            build_proc.processOutput()
            exitcode = build_proc.wait()
        except (KeyboardInterrupt, SystemExit):
            print "User Canceled Operation!"
            log.debug("Build canceled by user")
            raise
        finally:
            self.build_log.close()

        if exitcode == 0:
            print "Build %d Completed Successfully" % self.build_number
            log.debug("Build %d for history line: %s completed successfully" % (self.build_number, history_line))
        else:
            print "Build %d Failed" % self.build_number
            log.debug("Build %d for history line: %s FAILED" % (self.build_number, history_line))

    def notify_status(self, line):
        if not self.log_open:
            #TODO: need path to include history_lines somehow
            if not os.path.exists(self.build_info['logdir']):
                os.makedirs(self.build_info['logdir'])
            logfile = os.path.join(self.build_info['logdir'], ('build_%d.log' % self.build_number))
            self.build_log = open(logfile, "w")
            self.log_open = True
        if int((datetime.now() - self.start_time).total_seconds()) % 10 == 0:
            sys.stdout.write('.')
        self.build_log.write(line + '\n')

    def notify_timeout(self):
        if self.log_open:
            self.build_log.write("BUILD TIMED OUT\n")
        print "Build Timed Out"
        self.log.debug("Build Timed Out")

    def notify_finished(self):
        print "Build %d Finished!" % self.build_number

    def eval(self, history_line):
        self.perform_build(history_line)
        InteractiveEvaluator.eval(self, history_line)