import os
import optparse
import urlparse
import logging
import cProfile
import pstats

log = logging.getLogger(__name__)

import bisect_b2g
from bisect_b2g.repository import Project
from bisect_b2g.bisection import Bisection
from bisect_b2g.history import build_history
from bisect_b2g.evaluator import ScriptEvaluator, InteractiveEvaluator, InteractiveBuildEvaluator


class InvalidArg(Exception):
    pass


def local_path_to_name(lp):
    head, tail = os.path.split(lp)

    if tail.endswith('.git'):
        return tail[:-4]
    else:
        return tail


def uri_to_name(uri):
    uri_bits = urlparse.urlsplit(uri)
    host = uri_bits.netloc
    host, x, path_base = host.partition(':')
    path_full = uri_bits.path

    if path_base != '':
        path_full = path_base + path_full

    name = path_full.split('/')[-1]

    if name.endswith('.git'):
        name = name[:-4]

    return str(name)


def parse_arg(arg):
    """
    Parse an argument into a dictionary with the keys:
        'uri' - This is a URI to point to a repository.  If it is a local file,
                no network cloning is done
        'good' - Good changeset
        'bad' - Bad changeset
        'local_path' - This is the path on the local disk relative to
                       os.getcwd() that contains the repository

    The arguments that are parsed by this function are in the format:
        [GIT|HG][URI->]LOCAL_PATH@GOOD..BAD

    The seperators are '->', '..' and '@', quotes exclusive.  The URI and '->'
    are optional
    """
    arg_data = {}
    uri_sep = '@'
    rev_sep = '..'
    lp_sep = '->'

    if arg.startswith('HG'):
        vcs = 'hg'
        arg = arg[2:]
    elif arg.startswith('GIT'):
        vcs = 'git'
        arg = arg[3:]
    else:
        vcs = None  # Careful, this gets used below because we want to
        # share the URI parsing logic, but we do the vcs token up here

    # Let's tease out the URI and revision range
    uri, x, rev_range = arg.partition(uri_sep)
    if x != uri_sep:
        raise InvalidArg("Argument '%s' is not properly formed" % arg)

    # Now let's get the good and bad changesets
    arg_data['good'], x, arg_data['bad'] = rev_range.partition(rev_sep)
    if x != rev_sep:
        raise InvalidArg("Argument '%s' is not properly formed" % arg)

    if os.path.exists(uri):
        local_path = uri
    else:
        # Non-local URIs need to determine the name and local_path
        if lp_sep in uri:
            uri, x, local_path = uri.partition(lp_sep)
            name = uri_to_name(local_path)
        else:
            # This is the case where the local_path doesn't exist locally,
            # so we try to clone it into a sane location
            name = uri_to_name(uri)
            local_path = os.path.join(os.getcwd(), 'repos', name)

    # If the arg didn't start with a vcs token, we need to guess Git or Hg
    if vcs is None:
        git_urls = ('github.com', 'codeaurora.org', 'linaro.org',
                    'git.mozilla.org')
        hg_urls = ('hg.mozilla.org',)
        remote_uris = [('git', y) for y in git_urls]
        remote_uris.extend([('hg', y) for y in hg_urls])
        # Git can give itself away at times.  Yes, someone could have an hg
        # repo that ends with .git.  Wanna fight about it?
        if uri.startswith("git://") or uri.endswith(".git"):
            for y in hg_urls:
                if y in uri:
                    raise InvalidArg("Just because this URI starts with " +
                                     "git:// or ends with .git doesn't make " +
                                     "it a git repo.")
            vcs = 'git'
        else:
            for expected_vcs, remote_uri in remote_uris:
                if remote_uri in uri:
                    if vcs and vcs != expected_vcs:
                        raise InvalidArg(
                            "This URI seems to think that it's a " + vcs +
                            "but we just found a clue that it's a " +
                            expected_vcs + "because it contains " + remote_uri)
                    else:
                        vcs = expected_vcs
    if vcs:
        arg_data['vcs'] = vcs
    else:
        raise InvalidArg("Could not determine VCS system")

    arg_data['uri'] = uri
    arg_data['name'] = local_path_to_name(local_path)
    arg_data['local_path'] = local_path
    log.debug("Parsed '%s' to '%s'", arg, arg_data)
    return arg_data


def make_arg(arg_data):
    """ I am the reverse of parse_arg.  I am here in case someone else wants to
    generate these strings"""

    if uri_to_name(arg_data['local_path']) != arg_data['name']:
        raise InvalidArg(
            "the name in the arg_data dictionary is invalid: " +
            "'%s' != '%s'" % (arg_data['name'],
                              uri_to_name(arg_data['local_path'])))

    if arg_data.get('vcs') == 'git':
        arg = 'GIT'
    elif arg_data.get('vcs') == 'hg':
        arg = 'HG'
    else:
        raise InvalidArg("This arg_data is missing a valid VCS: %s" % arg_data)

    if arg_data['local_path'] != arg_data['uri']:
        arg += "%(uri)s->%(local_path)s" % arg_data
    else:
        arg += "%(local_path)s" % arg_data

    arg += "@%(good)s..%(bad)s" % arg_data

    return arg


def main():
    parser = optparse.OptionParser("%prog - I bisect repositories!")
    parser.add_option("--script", "-x", help="Script to run.  Return code 0 " +
                      "means the current changesets are good, Return code 1 " +
                      "means that it's bad", dest="script")
    parser.add_option("-o", "--output", help="File to write HTML output to",
                      dest="output_html", default="bisect.html")
    parser.add_option("-i", "--interactive", help="Interactively determine " +
                      "if the changeset is good",
                      dest="interactive", action="store_true")
    parser.add_option("-b", "--builds", help="Perform builds while evaluating",
                    dest="do_builds", action="store_true")
    parser.add_option("-v", "--verbose", help="Logfile verbosity",
                      action="store_true", dest="verbose")
    parser.add_option("--profile-output", dest="prof_out", default=None)
    parser.add_option("--build-workdir", help="Set working directory for building",
                      dest="build_workdir", default=None)
    parser.add_option("--build-logdir", help="Set logfile directory for build logs",
                      dest="build_logdir", default=None)
    parser.add_option("--build-env", help="Key Value pairs to construct env vars for build "+
                      "environment in this form: key=value,key=value", dest="build_env", default=None)
    opts, args = parser.parse_args()

    # Set up logging
    bisect_b2g_log = logging.getLogger(bisect_b2g.__name__)
    bisect_b2g_log.setLevel(logging.DEBUG)
    lh = logging.StreamHandler()
    lh.setLevel(logging.INFO)
    bisect_b2g_log.addHandler(lh)
    file_handler = logging.FileHandler('bisection.log')
    fmt = "%(asctime)s - %(levelname)s - %(filename)s/" + \
          "%(funcName)s:%(lineno)d - %(message)s"
    file_handler.setFormatter(logging.Formatter(fmt))
    bisect_b2g_log.addHandler(file_handler)

    if opts.verbose:
        file_handler.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)
        file_handler.setLevel(logging.INFO)

    if opts.script and opts.interactive:
        log.error("You can't specify a script *and* interactive mode")
        parser.print_help()
        parser.exit(2)
    elif opts.script and opts.do_builds:
        log.error("Building with script evaluator not implemented yet")
        parser.print_help()
        parser.exit(2)
    elif opts.script:
        evaluator = ScriptEvaluator(opts.script)
    elif opts.interactive and opts.do_builds:
        build_info = {'workdir': None, 'logdir': None, 'env': None}
        if opts.build_workdir:
            build_info['workdir'] = opts.build_workdir
        if opts.build_logdir:
            build_info['logdir'] = opts.build_logdir
        if opts.build_env:
            build_info['env'] = opts.build_env
        evaluator = InteractiveBuildEvaluator(build_info)
    else:
        evaluator = InteractiveEvaluator()

    projects = []

    if len(args) < 2:
        log.error("You must specify at least two repositories")
        parser.print_help()
        parser.exit()

    if opts.prof_out:
        pr = cProfile.Profile()
        pr.enable()
    for arg in args:
        try:
            repo_data = parse_arg(arg)
        except InvalidArg as ia:
            log.error(ia)
            parser.print_help()
            parser.exit(2)

        projects.append(Project(
            name=repo_data['name'],
            url=repo_data['uri'],
            local_path=repo_data['local_path'],
            good=repo_data['good'],
            bad=repo_data['bad'],
            vcs=repo_data['vcs'],
        ))
    combined_history = build_history(projects)
    bisection = Bisection(projects, combined_history, evaluator)
    bisection.write(opts.output_html)
    if opts.prof_out:
        pr.disable()
        with open(opts.prof_out, 'w+b') as f:
            ps = pstats.Stats(pr, stream=f)
            ps.strip_dirs()
            ps.sort_stats('cumulative', 'time')
            ps.print_stats()
    log.info("Found:")
    map(log.info, ["  * %s@%s" % (rev.prj.name, rev.hash)
                   for rev in bisection.found])
    log.info(
        "This was revision pair %d of %d total revision pairs" %
        (combined_history.index(bisection.found) + 1, len(combined_history))
    )


if __name__ == "__main__":
    main()
