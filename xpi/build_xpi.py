#!/usr/bin/python3
import sys
import os
import argparse
import tempfile
import shutil
import subprocess
import re
import fileinput
from collections import OrderedDict
import json
import hashlib

# Hack to combine two argparse formatters
class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass

parser = argparse.ArgumentParser(
    description='Build a Zotero XPI',
    formatter_class=CustomFormatter,
    epilog='''
Example: build_xpi -b 4.0 -s -x 4.0.1
  - Builds from the 4.0 branch
  - Builds zotero-build.xpi and update-build.rdf in ./build
  - Points install.rdf to zotero.org/download/update.rdf
  - Points update-build.rdf to zotero.org/download/zotero-4.0.1.xpi

Example: build_xpi -b 4.0 -s -x 4.0b2 -r beta
  - Builds from the 4.0 branch
  - Builds zotero-build.xpi and update-build.rdf in ./build
  - Points install.rdf to zotero.org/download/update-beta.rdf
  - Points update-build.rdf to zotero.org/download/zotero-4.0b2.xpi

Example: build_xpi -x trunk -r trunk --xpi-dir dev
  - Builds from the 4.0 branch
  - Builds zotero-build.xpi and update-build.rdf in ./build
  - Points install.rdf to zotero.org/download/dev/update-trunk.rdf
  - Points update-build.rdf to zotero.org/download/dev/zotero-trunk.xpi''')

parser.add_argument('-b', '--branch', default='master', help='Git branch or tag to build from')
parser.add_argument('-c', '--channel', default='release', help='channel to add to dev build version numbers (e.g., "beta")')
parser.add_argument('--xpi-suffix', '-x', metavar='SUFFIX', default='build', help='suffix of XPI referenced in update.rdf')
parser.add_argument('--rdf-suffix', '-r', metavar='SUFFIX', default='', help='suffix of update.rdf file to reference in install.rdf (e.g., "beta" for "update-beta.rdf")')
parser.add_argument('--xpi-dir', metavar='DIR', default='', help='extra directory to point to when referencing the XPI in update.rdf')
parser.add_argument('--build-suffix', metavar='SUFFIX', default='build', help='suffix of output XPI')
parser.add_argument('--repo-url', metavar='URL', default='https://github.com/zotero/zotero', help='Git URL to pull from')
parser.add_argument('--tmp-dir', metavar='DIR', default=tempfile.gettempdir(), help='temp directory')

args = parser.parse_args()

tmp_build_dir = os.path.join(args.tmp_dir, 'zotero-build')
# Remove tmp build directory if it already exists
if os.path.exists(tmp_build_dir):
    shutil.rmtree(tmp_build_dir)

def main():
    if args.xpi_suffix:
        args.xpi_suffix = "-" + args.xpi_suffix
    if args.rdf_suffix:
        args.rdf_suffix = "-" + args.rdf_suffix
    if args.build_suffix:
        args.build_suffix = "-" + args.build_suffix
    
    # Use 'build' subdirectory for source files and output
    build_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build')
    
    if not os.path.isdir(build_dir):
        raise Exception(build_dir + " is not a directory")
    
    # Create 'zotero' directory inside build directory if necessary
    src_dir = os.path.join(build_dir, 'zotero')
    if not os.path.isdir(src_dir):
        log(src_dir + " does not exist -- creating")
        os.mkdir(src_dir)
    
    os.chdir(src_dir)
    
    # If 'zotero' directory doesn't contain Zotero code, clone the repo into it
    if not os.path.exists(os.path.join(src_dir, '.git')):
        log("Cloning Zotero into " + src_dir)
        subprocess.check_call(['git', 'clone', '--recursive', args.repo_url])
    
    # Check out Zotero code
    #
    # Note: This script ignores fixed submodule versions and always pulls
    # the latest versions.
    subprocess.check_call(['git', 'checkout', args.branch])
    subprocess.check_call('git pull', shell=True)
    subprocess.check_call('git submodule init', shell=True)
    subprocess.check_call('git submodule update', shell=True)
    subprocess.check_call('git submodule foreach git pull origin master', shell=True)
    
    if not os.path.exists('install.rdf'):
        raise FileNotFoundError("install.rdf not found in {0}".format(src_dir))
    
    # Extract version number from install.rdf
    with open('install.rdf') as f:
        rdf = f.read()
    m = re.search('version>([0-9].+)\\.SOURCE</', rdf)
    if not m:
        raise Exception("Version number not found in install.rdf")
    version = m.group(1)
    commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).decode(encoding="utf-8").strip()
    
    os.mkdir(tmp_build_dir)
    tmp_src_dir = os.path.join(tmp_build_dir, 'zotero')
    
    # Export a clean copy of the source tree
    subprocess.check_call(['rsync', '-a', '--exclude', '.git*', '.' + os.sep, tmp_src_dir + os.sep])
    
    # Make sure rsync worked
    d = os.path.join(tmp_src_dir, 'chrome')
    if not os.path.isdir(d):
        raise FileNotFoundError(d + " not found")
    
    log("Deleting CSL locale support files")
    subprocess.check_call(['find',
        os.path.normpath(tmp_src_dir + '/chrome/content/zotero/locale/csl/'),
        '-mindepth', '1', '!', '-name', '*.xml', '!', '-name', 'locales.json', '-print', '-delete'])
    
    translators_dir = os.path.join(tmp_src_dir, 'translators')
    translators_temp_dir = os.path.join(tmp_src_dir, 'translators2')
    os.mkdir(translators_temp_dir)
    
    # Move deleted.txt out of translators directory
    f = os.path.join(translators_dir, 'deleted.txt')
    if os.path.exists(f):
        shutil.move(f, tmp_src_dir)
    
    # Copy translators to numbered files and create JSON index
    i = 0
    index = OrderedDict()
    for f in sorted((f for f in os.listdir(translators_dir)), key=str.lower):
        f = os.path.join(translators_dir, f)
        numbered_file = str(i) + ".js"
        shutil.copy(f, os.path.join(translators_temp_dir, numbered_file))
        
        with open(f, 'r') as f:
            contents = f.read()
        # Parse out the JSON metadata block
        m = re.match('^\s*{[\S\s]*?}\s*?[\r\n]', contents)
        if not m:
            raise Exception("Metadata block not found in " + f)
        metadata = json.loads(m.group(0))
        index[numbered_file] = {
            "translatorID": metadata["translatorID"],
            "label": metadata["label"],
            "lastUpdated": metadata["lastUpdated"]
        }
        
        i += 1
    
    # TODO: Unminify translator FW lines
    
    with open(os.path.join(tmp_src_dir, 'translators.json'), 'w') as f:
        json.dump(index, f, indent=True, ensure_ascii=False)
    
    # Swap in new translator directory with numbered files
    shutil.rmtree(translators_dir)
    os.rename(translators_temp_dir, translators_dir)
    
    install_file = os.path.join(tmp_src_dir, 'install.rdf')
    update_file = os.path.join(tmp_src_dir, 'update.rdf')
    
    log('''
======================================================

Original install.rdf:
''')
    dump_file(install_file)
    log('''
Original update.rdf:
''')
    dump_file(update_file)
    log('''
 ======================================================

''')
    
    # Modify install.rdf and update.rdf as necessary
    
    # The dev build revision number is stored in build/lastrev.
    #
    # If we're including it, get the current version number and increment it.
    if args.channel != "release":
        lastrev_file = os.path.join(build_dir, 'lastrev-' + version)
        if not os.path.exists(lastrev_file):
            with open(lastrev_file, 'w') as f:
                f.write("0")
                rev = 1
        else:
            with open(lastrev_file, 'r') as f:
                rev = int(f.read()) + 1

    
    if args.channel == "release":
        rev_sub_str = ""
    else:
        rev_sub_str = "-{0}.r{1}+{2}".format(args.channel, str(rev), commit_hash)
    if args.xpi_dir:
        xpi_dir = args.xpi_dir + '/'
    else:
        xpi_dir = ''
    for line in fileinput.FileInput(install_file, inplace=1):
        line = line.replace('.SOURCE', rev_sub_str)
        line = line.replace('update-source.rdf', xpi_dir + 'update' + args.rdf_suffix + '.rdf')
        print(line, file=sys.stdout, end='')
    for line in fileinput.FileInput(update_file, inplace=1):
        line = line.replace(".SOURCE", rev_sub_str)
        line = line.replace('zotero.xpi', xpi_dir + 'zotero' + args.xpi_suffix + '.xpi')
        print(line, file=sys.stdout, end='')
    
    # Move update.rdf out of code root
    shutil.move(update_file, tmp_build_dir)
    tmp_update_file = os.path.join(tmp_build_dir, 'update.rdf')
    
    # Create XPI
    os.chdir(tmp_src_dir)
    tmp_xpi_file = os.path.join(tmp_build_dir, 'zotero' + args.build_suffix + '.xpi')
    subprocess.check_call([
        'zip',
        '-r',
        tmp_xpi_file,
        '.'])
    
    # Add SHA1 of XPI to update.rdf
    sha1 = sha1file(tmp_xpi_file)
    for line in fileinput.FileInput(tmp_update_file, inplace=1):
        line = line.replace("sha1:", "sha1:" + sha1)
        print(line, file=sys.stdout, end='')
    
    log('''
======================================================

Modified install.rdf:
''')
    dump_file(install_file)
    log('''
Modified update.rdf:
''')
    dump_file(tmp_update_file)
    log('''
 ======================================================

''')
    
    # Move files to build directory
    os.rename(tmp_xpi_file, os.path.join(build_dir, 'zotero' + args.build_suffix + '.xpi'))
    os.rename(tmp_update_file, os.path.join(build_dir, 'update' + args.build_suffix + '.rdf'))
    
    log("")
    log("zotero{0}.xpi and update{0}.rdf saved to {1}".format(args.build_suffix, build_dir))
    log("")
    
    # Update lastrev file with new revision number
    if args.channel != "release":
        with open(lastrev_file, 'w') as f:
            f.write(str(rev))


def dump_file(f):
    with open(f, 'r') as f:
        log(f.read())


def log(msg):
    print(msg, file=sys.stdout)


def sha1file(f):
    sha1 = hashlib.sha1()
    with open(f, 'rb') as f:
        sha1.update(f.read())
    return sha1.hexdigest()


if __name__ == '__main__':
    try:
        main()
    # Clean up
    finally:
        if os.path.exists(tmp_build_dir):
            shutil.rmtree(tmp_build_dir)
