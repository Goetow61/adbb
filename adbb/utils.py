#!/bin/env python3
import argparse
import os
import re
import shutil

import adbb

# These extensions are considered video types
SUPPORTED_FILETYPES = [
        'mkv',
        'avi',
        'mp4',
        'ogm',
        'wmv',
        'm4v',
        'webm',
        ]

# Specials-directories as defined by jellyfin
JELLYFIN_SPECIAL_DIRS = [
        'behind the scenes',
        'deleted scenes',
        'interviews',
        'scenes',
        'samples',
        'shorts',
        'featurettes',
        'extras',
        'trailers',
        ]

# matches groupnames from paranthesis at start of filename
RE_GROUP_START = re.compile(r'^[\(\[]([^\d\]\)]+)[\)\]].*')
# matches groupnames from paranthesis at end of filename
RE_GROUP_END = re.compile(r'.*[\(\[]([^\d\]\)]+)[\)\]]\.\w{3,4}$')

# matches anidb's default english episode names
RE_DEFAULT_EPNAME = re.compile(r'Episode S?\d+', re.I)


def arrange_anime_args():
    parser = argparse.ArgumentParser(description="Rearange video files in directory to jellyfin-parsable anime episodes")
    parser.add_argument(
            '-n', '--dry-run',
            help="do not actually move stuff..",
            action='store_true'
            )
    parser.add_argument(
            '-d', '--debug',
            help='show debug information from adbb',
            action='store_true'
            )
    parser.add_argument(
            '-u', '--username',
            help='anidb username',
            )
    parser.add_argument(
            '-p', '--password',
            help='anidb password'
            )
    parser.add_argument(
            '-s', '--sql-url',
            help='sqlalchemy compatible sql URL',
            default=f'sqlite:///{os.path.expanduser("~/.adbb.db")}'
            )
    parser.add_argument(
            '-a', '--authfile',
            help="Authfile (.netrc-file) for credentials"
            )
    parser.add_argument(
            "paths",
            help="Directories containing episodes",
            nargs="+"
            )
    parser.add_argument(
            "-t", '--target-dir',
            help="Where to put the stuff after parsing...",
            )
    return parser.parse_args()

def create_filelist(paths, recurse=True):
    files = []
    for path in paths:
        # find all files with supported file extensions and add them to
        # our working list if input is a directory.
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d.lower() not in JELLYFIN_SPECIAL_DIRS]
                files.extend([ os.path.join(root, x) for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ])
                if not recurse:
                    break
        else:
            files.append(path)
    return files

def arrange_files(filelist, target_dir=None, dry_run=False):
    for f in filelist:
        epfile = adbb.File(path=f)
        if epfile.group:
            # replace any / since it's not supported for filenames in *nix
            group = epfile.group.name.replace('/', '⁄')
        else:
            # if anidb don't know about the group we try to parse the filename
            # look for [] or ()-blocks at start or end of filename, and assume this
            # is the groupname.
            m = re.match(RE_GROUP_START, os.path.split(f)[-1])
            if not m:
                m = re.match(RE_GROUP_END, os.path.split(f)[-1])
            if m:
                # since we matched on filename previously there is no need to
                # replace any chars here.
                group = m.group(1)
            else:
                group = "unknown"

        # how many characters in an episode number? will probably return 1 (<10
        # episodes), 2 (<100 episodes), 3 (<1000 episodes) or 4 (hello Conan
        # and Luffy)
        epnr_minlen = len(str(max(epfile.anime.highest_episode_number, epfile.anime.nr_of_episodes)))

        aname = epfile.anime.title.replace('/', '⁄')
        ext = f.rsplit('.')[-1]
        if epfile.anime.nr_of_episodes == 1:
            # personal definition of movies: exactly 1 (main-)episode.
            # Name movies after title and append groupname to mark different
            # "versions" of the movie:
            #   https://jellyfin.org/docs/general/server/media/movies.html
            newname = f'{aname} [{group}].{ext}'
        else:
            # Use the first found of these titles:
            # * romanji
            # * kanji
            # * english (if set to anything other than default "Episode XX")
            #
            # If no title was found we do not append it to the filename
            if epfile.episode.title_romaji:
                title = f' - {epfile.episode.title_romaji}'.replace('/', '⁄')
            elif epfile.episode.title_kanji:
                title = f' - {epfile.episode.title_kanji}'.replace('/', '⁄')
            elif epfile.episode.title_eng and not re.match(RE_DEFAULT_EPNAME, epfile.episode.title_eng):
                title = f' - {epfile.episode.title_eng}'.replace('/', '⁄')
            else:
                title = ''

            try:
                # regular episodes converts nicely to integers...
                int(epfile.multiep[0])
                season='1'
            except ValueError:
                # ... while specials doesn't (contains characters in episode number)
                season='0'
                epnr_minlen = len(str(epfile.anime.special_ep_count))

            # check if file contains multiple episodes
            if len(epfile.multiep) > 1:
                mi = int(epfile.multiep[0].strip('SCT'))
                ma = int(epfile.multiep[-1].strip('SCT'))
                epstr = f'{mi:0{epnr_minlen}d}-{ma:0{epnr_minlen}d}'
            else:
                epstr = f'{int(epfile.multiep[0].strip("SCT")):0{epnr_minlen}d}'

            newname = f'[{group}] {aname} S{season}E{epstr}{title}.{ext}'

        if target_dir:
            # Escape slash as usual, but for also remove dot-prefixes because
            # "Hidden" directories are a hastle; sorry .hack//...
            anime_dirname = epfile.anime.title.replace('/', '⁄').lstrip('.')
            movie_subdir = 'Movies'
            series_subdir = 'Series'
            # If target directory has separate subdirs for movies and series;
            # place the files there
            if os.path.isdir(os.path.join(target_dir, movie_subdir)) and epfile.anime.nr_of_episodes == 1:
                newname = os.path.join(target_dir, movie_subdir, anime_dirname, newname)
            elif os.path.isdir(os.path.join(target_dir, series_subdir)):
                newname = os.path.join(target_dir, series_subdir, anime_dirname, newname)
            else:
                newname = os.path.join(target_dir, anime_dirname, newname)


        # no action if file is already properly named and in the right place
        if f != newname:
            adbb.log.info(f'Moving "{f}" -> "{newname}"')
            if not dry_run:
                nd, nh = os.path.split(newname)
                try:
                    os.mkdir(nd)
                except FileExistsError:
                    pass
                shutil.move(f, newname)
                od, oh = os.path.split(f)
                # Make sure extras-directories are moved if it's all that is
                # left in the old directory
                for root, dirs, files in os.walk(od):
                    if not files and all([x.lower() in JELLYFIN_SPECIAL_DIRS for x in dirs]):
                        for d in dirs:
                            shutil.move(os.path.join(root, d), os.path.join(nd, d))
                    break
                try:
                    os.rmdir(od)
                except OSError:
                    pass
                if not epfile.lid:
                    epfile.update_mylist(watched=False, state='on hdd')

def arrange_anime():
    args = arrange_anime_args()
    filelist = create_filelist(args.paths)
    adbb.init(args.sql_url, api_user=args.username, api_pass=args.password, debug=args.debug, netrc_file=args.authfile)
    arrange_files(filelist, target_dir=args.target_dir, dry_run=args.dry_run)
    adbb.close()

if __name__ == '__main__':
    main()