# -*- coding: utf-8 -*-
"""Downloads stories from the website literotica.com and compiles them to an .epub file.

Note: The author of this scipt has no connection to literotica.com, this
is just a hobby project.

To fetch an entire series, just provide the URL of one of the stories.
(The program will download the list of story submissions on the
memberpage of the author and get the links to other parts of the series.)

written for python 2.6, 2.7, >=3

License: CC0 (public domain)
"""

from __future__ import unicode_literals

import argparse
import hashlib
import io
import mimetypes
import os
import re
import sys
import uuid
import zipfile

# python 2 / 3 compatibility code
try:
    import urllib.request as compat_urllib_request
    import urllib.parse as compat_urllib_parse
except ImportError: # python 2
    import urllib2 as compat_urllib_request
    import urlparse as compat_urllib_parse
if sys.version < '3':
    text_type = unicode
    binary_type = str
else:
    text_type = str
    binary_type = bytes

VERSION = '0.1'     # the program version

args = None         # command line arguments
url_mem_cache = {}  # cache for downloaded websites

all_oneshots = None # all the oneshot stories found, global for debugging
all_series = None   # all the series found, global for debugging

def main():
    global all_oneshots
    global all_series
    
    parse_commandline_arguments()

    story_html = fetch_url(args.url)
    page_id = extract_id(args.url)
    
    (title, author, memberpage_url) = parse_story_header(story_html)
    debug("title: '{}', author: '{}', memberpage: '{}'".format(title, author, memberpage_url))
    
    memberpage = fetch_url(memberpage_url)
    (all_oneshots, all_series) = parse_story_list(memberpage)
    
    if args.debug:
        debug('ALL STORIES BY AUTHOR {}:'.format(author))
        for st in all_oneshots:
            debug('{}'.format(st))
        debug('ALL SERIES:'.format(author))
        for series in all_series:
            debug('{}'.format(series.title))
            for st in series.stories:
                debug('    {}'.format(st))
    
    found_story = None
    found_series = None
    for st in all_oneshots:
        if extract_id(st.url) == page_id:
            found_story = st
            break
    
    if not found_story:
        for series in all_series:
            for st in series.stories:
                if extract_id(st.url) == page_id:
                    found_story = st
                    found_series = series
                    break
            if found_series: break
    
        if not found_series: error("Couldn't find story on members page")
        
        if args.debug:
            debug(found_series.title)
            for st in found_series.stories:
                debug('  {}'.format(st))

    if args.author: author = args.author
    if found_series and not args.single:
        make_epub_from_story_or_series(found_series, author)
    else:
        make_epub_from_story_or_series(found_story, author)
    
def parse_commandline_arguments():
    """Parse the command line arguments.
    """
    global args
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='URL of the story, or one of the stories in the series')
    parser.add_argument('-a', '--author', help='override the author in the epub metadata')
    parser.add_argument('-t', '--title', help='override the title in the epub metadata and default file name')
    parser.add_argument('-o', '--output', metavar='FILENAME', help='set output file name (optional, otherwise story title is used)')
    parser.add_argument('-s', '--single', action='store_true', help='do not attempt to download the entire series (if it is a series) but just this one story')
    parser.add_argument('--noteaser', action='store_true', help='do not include the one line teaser in the table of contents')
    parser.add_argument('-v', '--verbose', action='store_true', help='output more information')
    parser.add_argument('-d', '--debug', action='store_true', help='output debug information')
    parser.add_argument('--disk-cache-path', metavar='PATH', help='Path for the disk cache (optional, usually not required). If this option is specified, downloaded websites are cached in a file and loaded from disk in subsequent runs (when this option is used again with the same path). This is mainly useful for testing, to avoid repeated downloads. Without this option, litepubify keeps everything in memory and only writes the final epub file to disk.')
    args = parser.parse_args()

def parse_story_header(html):
    """Parses the header of the story html to find title, author and the link to the author's memberpage.
    
    Args:
      html (text): the full html text of the story
    
    Returns:
      title, author and memberpage url as a 3-tuple
      
    """
    header_match = re.search(r'<div class="b-story-header">(.*?)</div>', html, flags=re.DOTALL)
    if not header_match:
        error("Cannot find header in html.")
    header_match2 = re.search(r'<h1>(.*?)</h1>.*?<a href="(.*?)">(.*?)</a>', header_match.group(1), flags=re.DOTALL)
    if not header_match2:
        error("Cannot parse header.")
    title = header_match2.group(1)
    memberpage_url = header_match2.group(2)
    memberpage_url = re.sub(r'&amp;', r'&', memberpage_url)
    memberpage_url = re.sub(r'^//', r'http://', memberpage_url)
    author = header_match2.group(3)
    return (title, author, memberpage_url)

def make_epub_from_story_or_series(s, author):
    """Make epub file from story or series.
    
    Args:
        s (Story or Series): the story or series to make an epub from
    
    """
    book = EpubBook()
    book.title = s.title
    if args.title: book.title = args.title
    book.creator = author

    if isinstance(s, Story):
        add_story_to_ebook(s, 'content.html', book)
    else:
        i = 1
        for st in s.stories:
            add_story_to_ebook(st, 'part{0:02d}.html'.format(i), book)
            i += 1

    path = re.sub(r'[^\w_. -]', r'_', book.title, flags=re.UNICODE)
    arch_filename = path + '.epub'
    #book.make_epub_unpacked(path)      # for testing
    if args.output:
        arch_filename = args.output
    book.make_epub(arch_filename)

def add_story_to_ebook(st, filename, book):
    """Add a story to an ebook.
    
    Args:
        st (Story): the story
        filename (text): filename for the section in the ebook
        book (EpubBook): the book
    """
    txt = get_story_text(st)
    txt = make_tags_lowercase(txt)
    txt = TITLE_TEMPLATE.format(title=st.title, author=st.author) + txt
    html = TXT_HTML_TEMPLATE.format(title=book.title, content=txt)
    book.add_html(st.title, st.teaser, html, filename)
    
def make_tags_lowercase(html):
    """Convert tags like <I>...</I> to lowercase version <i>...</i>.
    
        This has to be done for xhtml 1.1 compliance.
        The method with regex is sort of hackish, but should work for most cases.
        
        Args:
          html (text): the html text
          
        Returns:
          unicode: the fixed html text
    """
    def tag_lower(tag_match):
        t = tag_match.group(0)
        t = re.sub(r'<\s*/?\s*(\w+)[\s/>]', lambda s: s.group(0).lower(), t)
        return re.sub(r'\w+="', lambda s: s.group(0).lower(), t)
    return re.sub(r'<.*?>', tag_lower, html)
    

def parse_story_list(html):
    """Parse the list of stories from the submissions section of the author's memberpage.
    """
    author_match = re.search(r'<span class="unameClick"><a .*?>(.*?)</a>.*?</span>', html, flags=re.DOTALL)
    if not author_match:
        error("Cannot determine author on member page.")
    author = author_match.group(1)
    
    subm_table_match = re.search(r'<table.*?>.*?<col .*?(<tr .*?)</table>', html, flags=re.DOTALL)
    if not subm_table_match:
        error("Cannot find list of submissions on member page.")
    
    trs = re.findall(r'(<tr.*?</tr>)', subm_table_match.group(1), re.DOTALL)
    all_series = []
    all_oneshots = []
    series = None
    story = None
    for tr in trs:
        if tr.startswith(r'<tr class="ser-ttl">'):    # series title
            series_title_match = re.search(r'<strong>(.*?)</strong>', tr)
            if not series_title_match:
                error("Cannot find series title: '{}'".format(tr))
            series = Series()
            series.title = series_title_match.group(1)
            series.title = re.sub(r': \d+ Part Series$', '', series.title)
            series.author = author
            all_series.append(series)
        elif tr.startswith(r'<tr class="sl">') or tr.startswith(r'<tr class="root-story'):
            tds = re.findall(r'<td.*?>(.*?)</td>', tr, re.DOTALL)
            if len(tds) != 4: error("Unexpected number of fields (expected 4 but where {}): '{}'".format(len(tds), tr))
            
            td0_match = re.search(r'<a .*?href="(.*?)">(.*?)</a>.*?[(](.*?)[)]', tds[0])
            if not td0_match: error("Couldn't match 1st field: '{}'".format(tds[0]))
            story = Story()
            story.url = td0_match.group(1)
            story.url = re.sub(r'^//', r'http://', story.url)
            story.title = td0_match.group(2)
            story.title = re.sub(r'<span>|</span>|<!--.*?-->', '', story.title)
            story.author = author
            story.rating = td0_match.group(3)
            
            td1_match = re.search(r'^\s*([^<]*)(<|$)', tds[1], flags=re.DOTALL | re.UNICODE)
            if not td1_match: error("Couldn't match 2nd field: '{}'".format(tds[1]))
            story.teaser = td1_match.group(1)
            story.teaser = story.teaser.strip()
            if re.search(r'ico_h.gif', tds[1]):
                story.hot = True
            else:
                story.hot = False
            
            td2_match = re.search(r'<span>(.*?)</span>', tds[2])
            story.category = td2_match.group(1)
            
            td3_match = re.search(r'\s*(.+)\s*', tds[3])
            story.date = td3_match.group(1)
            
            if tr.startswith(r'<tr class="sl">'):
                series.stories.append(story)
                story = None
            else:
                series = None
                all_oneshots.append(story)
                story = None
        elif tr.startswith(r'<tr class="st-top">'):     # ignore
            pass
        else:
            error("Unkown row type: '{}'".format(tr))

    return (all_oneshots, all_series)
        

def extract_id(url):
    """Extract the story id from a URL.
    
    Args:
      url: the URL
    
    Returns:
      the story id (the last part in the url path component)
    
    """
    o = compat_urllib_parse.urlparse(url)
    p = o.path
    p = re.sub('/$', '', p)
    idx = p.rfind('/')
    if idx == -1: error("unexpected url: {}".format(url))
    return p[idx+1:]
    
def get_story_text(st):
    html = fetch_url(st.url) # assuming url leads to first page and has no query part
    sel_match = re.search(r'<div class="b-pager-pages">(.*?)</div>', html)
    if not sel_match: error("Couldn't find page selection part.")
    vals = re.findall('<option value=".*?">(\d+)</option>', sel_match.group(1))
    if not vals: # just one page
        vals = ['1']
    complete_text = None
    for v in vals:
        url = st.url + '?page=' + v
        if v == '1':
            url = st.url
        html = fetch_url(url)
        text_match = re.search(r'<div class="b-story-body-x.*?">.*?<div>(.*?)</div>', html, re.DOTALL)
        if not text_match: error("Couldn't find text body.")
        text = text_match.group(1)
        text = text.strip()
        strip_outer_p_match = re.search(r'^<p>(.*)</p>$', text, re.DOTALL)
        if strip_outer_p_match:
            text = strip_outer_p_match.group(1)
        if complete_text == None:
            complete_text = text
        else:
            complete_text += '\n\n' + text

    if not complete_text:
        warning('Unable to extract test for {}.'.format(st.url))
    complete_text = '<p>{}</p>'.format(complete_text)

    return complete_text
    

class FrozenClass(object):
    """Auxiliary base class to prevent access to attributes that haven't been set in __init__
    """
    __isfrozen = False
    def __setattr__(self, key, value):
        if self.__isfrozen and not hasattr(self, key):
            raise TypeError("key not set: %s; %r of type %s is a frozen class" % (key, self, type(self)) )
        object.__setattr__(self, key, value)

    def _freeze(self):
        self.__isfrozen = True



class Story(FrozenClass):
    """A single story.
    
    Attributes:
      title (text): the title of the story
      teaser (text): a one line description
      author (text): the author
      url: download url
      rating: the rating
      hot (bool): if the story is rated as hot or not
      category (text): a category
      date: date of publication

    """
    def __init__(self):
        self.title = None
        self.teaser = None
        self.author = None
        self.url = None
        self.rating = None
        self.hot = None
        self.category = None
        self.date = None
        self._freeze()
    
    def __repr__(self):
        return '<"{}" - "{}" ({}) {}{} - {}, {}>'.format(self.title, self.teaser, self.rating, 'H ' if self.hot else '', self.url, self.category, self.date)
        return str(vars(self))
        
class Series(FrozenClass):
    """A series of multiple stories.
    
    Attributes:
      title (text): the title of the series
      author (text): the author of the series
      stories list(Story): the list of stories of the series
      
    """
    def __init__(self):
        self.title = None
        self.author = None
        self.stories = []
        self._freeze()

    def __repr__(self):
        return str(self.stories)
    

def fetch_url(url):
    """Download contents of a webpage.
    
    It does not check the encoding and simply
    assumes the document is UTF-8 encoded.

    Args:
        url: the URL of the webpage

    Returns:
        text: The content of the page
    """
    global url_mem_cache
    if url in url_mem_cache:
        return url_mem_cache[url]
    if args.disk_cache_path:
        path = os.path.join(args.disk_cache_path, url_to_filepath_hash(url))
        if (os.path.isfile(path)):
            txt = io.open(path, 'rb').read()
            utxt = txt.decode('UTF-8')
            url_mem_cache[url] = utxt
            return utxt
    verbose("downloading '{}'...".format(url))
    req = compat_urllib_request.Request(url, headers={ 'User-Agent': get_user_agent() })
    response = compat_urllib_request.urlopen(req)
    txt = response.read()
    if args.disk_cache_path:
        f = io.open(path, 'wb')
        f.write(txt)
        f.close()
    utxt = txt.decode('UTF-8')
    url_mem_cache[url] = utxt
    return utxt
    
def url_to_filepath_hash(url):
    salted = url+'la;l;vdoids'
    return hashlib.sha224(salted.encode('UTF-8')).hexdigest()

class EpubSection(FrozenClass):
    """One section / chapter of the ebook.
    
    Attributes:
      id (text): an id that is generated and used internally
      title (text): the title of the section / chapter
      teaser (text): a one line description which is included in the t.o.c.
      html (text): the html content
      filename (text): the filename, e.g. 'part1.html'
      
    """
    def __init__(self):
        self.id = ''
        self.title = ''
        self.teaser = ''
        self.html = ''
        self.filename = ''
        self._freeze()
    
class EpubBook(FrozenClass):
    def __init__(self):
        self.root_dir = ''
        self.UUID = uuid.uuid1()
        self.sections = []
        self.title = ''
        self.creator = ''
        self._freeze()

    def add_html(self, title, teaser, html, filename):
        """
        Add a new html file as a section / chapter of the epub.
        
        Args:
          title (text): the title of the section / chapter
          teaser (text): a one line description which is included in the t.o.c.
          html (text): the html content
          filename (text): the filename, e.g. 'part1.html'
        """
        section = EpubSection()
        section.id = 'html_%d' % (len(self.sections) + 1)
        section.filename = filename
        section.html = html
        section.title = title
        section.teaser = teaser
        self.sections.append(section)

    def _write_mimetype(self, writer):
        writer.write('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        
    def _write_items(self, writer):
        for section in self.sections:
            writer.write(
                os.path.join('OEBPS', section.filename),
                section.html)

    def _write_container_xml(self, writer):
        writer.write(os.path.join('META-INF', 'container.xml'), CONTAINER_TEMPLATE)

    def _write_content_opf(self, writer):
        manifest = ''
        spine = ''
        for section in self.sections:
            manifest += MANIFEST_ITEM_TEMPLATE.format(id=section.id, filename=section.filename)
            spine += SPINE_ITEM_TEMPLATE.format(id=section.id)
        txt = CONTENT_TEMPLATE.format(title=self.title, creator=self.creator, uuid=self.UUID, manifest=manifest, spine=spine)
        writer.write(os.path.join('OEBPS', 'content.opf'), txt)

    def _write_toc_ncx(self, writer):
        nav_points = ''
        i = 1
        for section in self.sections:
            title = section.title
            if not args.noteaser and section.teaser:
                title += ' - ' + section.teaser
            nav_points += NAV_POINT_TEMPLATE.format(id=section.id, playorder=i, title=title, filename=section.filename)
            i += 1
        writer.write(os.path.join('OEBPS', 'toc.ncx'), NCX_TEMPLATE.format(uuid=self.UUID, title=self.title, nav=nav_points))
    
    def write_all(self, writer):
        self._write_mimetype(writer)
        self._write_items(writer)
        self._write_container_xml(writer)
        self._write_content_opf(writer)
        self._write_toc_ncx(writer)
    
    def make_epub(self, filename):
        """Create the .epub file.
        Args:
            filename (text): the full filename, e.g. '/path/to/mybook.epub'
        """
        fzip = zipfile.ZipFile(filename, 'w')
        self.write_all(ZipWriter(self, fzip))
        fzip.close()

    def make_epub_unpacked(self, root_dir):
        """Create all the files for the epub archive in a directory structure (unpacked).
        Args:
            root_dir (text): the directory where to put the epub content
        """
        self.root_dir = root_dir
        self.write_all(FileWriter(self))

class FileWriter(FrozenClass):
    """Writes text to a file.
    """
    def __init__(self, ebook):
        self.ebook = ebook
        self._freeze()
        
    def write(self, path, txt, compress_type=zipfile.ZIP_STORED):
        fullpath = os.path.join(self.ebook.root_dir, path)
        dirpath = os.path.split(fullpath)[0]
        try:
            os.makedirs(dirpath)
        except OSError:
            pass
        fout = io.open(fullpath, 'w', encoding='UTF-8')
        fout.write(txt)
        fout.close()

class ZipWriter(FrozenClass):
    """Writes text inside a zip file.
    """
    def __init__(self, ebook, zipfile):
        self.zipfile = zipfile
        self.ebook = ebook
        self._freeze()
        
    def write(self, path, txt, compress_type=zipfile.ZIP_STORED):
        self.zipfile.writestr(path, txt.encode('UTF-8'), compress_type)

def get_user_agent():
    return "litepubify {}".format(VERSION)

def verbose(msg):
    """Helper function to output verbose messages in case they have been activated.
    """
    if args.verbose or args.debug:
        print(msg)

def debug(msg):
    """Helper function to output debug messages in case they have been activated.
    """
    if args.debug:
        print(msg)

def warning(msg):
    """Helper function to issue a warning.
    """
    print("Warning: "+msg)

def error(msg):
    """Helper function to raise an error.
    """
    if isinstance(msg, text_type):
        msg = msg.encode('UTF-8')
    raise Exception(msg)

CONTAINER_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

CONTENT_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf"
            xmlns:dc="http://purl.org/dc/elements/1.1/"
            unique-identifier="bookid" version="2.0">
  <metadata>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
    <dc:identifier id="bookid">urn:uuid:{uuid}</dc:identifier>
    <dc:language>en-US</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>{manifest}
  </manifest>
  <spine toc="ncx">{spine}
  </spine>
</package>"""

MANIFEST_ITEM_TEMPLATE = """
    <item id="{id}" href="{filename}" media-type="application/xhtml+xml"/>"""

SPINE_ITEM_TEMPLATE = """
    <itemref idref="{id}"/>"""

NCX_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"
                 "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid"
content="urn:uuid:{uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{title}</text>
  </docTitle>
  <navMap>{nav}
  </navMap>
</ncx>"""

NAV_POINT_TEMPLATE = """
    <navPoint id="{id}" playOrder="{playorder}">
      <navLabel>
        <text>{title}</text>
      </navLabel>
      <content src="{filename}"/>
    </navPoint>"""

TXT_HTML_TEMPLATE = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>{title}</title>
  </head>
  <body>
{content}
  </body>
</html>"""


TITLE_TEMPLATE = """<h1>{title}</h1>
<p>by <i>{author}</i></p>
<hr />
"""

main()








