#
# Copyright (c) nexB Inc. and others. All rights reserved.
# purldb is a trademark of nexB Inc.
# SPDX-License-Identifier: Apache-2.0
# See http://www.apache.org/licenses/LICENSE-2.0 for the license text.
# See https://github.com/nexB/purldb for support or download.
# See https://aboutcode.org for more information about nexB OSS projects.
#

import binascii
import codecs
import re

from commoncode import filetype
from licensedcode.tokenize import ngrams
from typecode.contenttype import get_type

from matchcode_toolkit.halohash import BitAverageHaloHash


# A collection of directory fingerprints that we want to avoid
IGNORED_DIRECTORY_FINGERPRINTS = [
    # This is both the directory content and directory structure fingerprint for
    # an empty directory.
    '0000000000000000000000000000000000000000',
]


def _create_directory_fingerprint(inputs):
    """
    Return a 128-bit BitAverageHaloHash fingerprint in hex from `inputs`
    """
    inputs = [i.encode('utf-8') for i in inputs if i]
    bah128 = BitAverageHaloHash(inputs, size_in_bits=128).hexdigest()
    inputs_count = len(inputs)
    inputs_count_hex_str = '%08x' % inputs_count
    bah128 = bah128.decode('utf-8')
    directory_fingerprint = inputs_count_hex_str + bah128
    return directory_fingerprint


def create_content_fingerprint(resources):
    """
    Collect SHA1 strings from a list of Resources (`resources`) and create a
    directory fingerprint from them
    """
    features = [r.sha1 for r in resources if r.sha1]
    return _create_directory_fingerprint(features)


def _get_resource_subpath(resource, top):
    """
    Return the subpath of `resource` relative to `top` from `codebase`

    For example:

    top.path = 'foo/bar/'
    resource.path = 'foo/bar/baz.c'

    The subpath returned would be 'baz.c'
    """
    _, _, subpath = resource.path.partition(top.path)
    subpath = subpath.lstrip('/')
    return subpath


def create_structure_fingerprint(directory, children):
    """
    Collect the subpaths of children Resources of Resource `directory` and
    create a fingerprint from them
    """
    features = []
    for child in children:
        if not child.path:
            continue
        child_subpath = _get_resource_subpath(child, directory)
        if not child.size:
            rounded_child_size = 0
        else:
            rounded_child_size = int(child.size / 10) * 10
        path_feature = str(rounded_child_size) + child_subpath
        features.append(path_feature)
    return _create_directory_fingerprint(features)


def _compute_directory_fingerprints(directory, codebase):
    """
    Compute fingerprints for `directory` from `codebase`
    """
    # We do not want to add empty files to our fingerprint
    children = [r for r in directory.walk(codebase) if r.is_file and r.size]
    if len(children) <= 1:
        return

    directory_content_fingerprint = create_content_fingerprint(children)
    if hasattr(directory, 'directory_content_fingerprint'):
        directory.directory_content_fingerprint = directory_content_fingerprint
    else:
        directory.extra_data['directory_content'] = directory_content_fingerprint

    directory_structure_fingerprint = create_structure_fingerprint(directory, children)
    if hasattr(directory, 'directory_structure_fingerprint'):
        directory.directory_structure_fingerprint = directory_structure_fingerprint
    else:
        directory.extra_data['directory_structure'] = directory_structure_fingerprint

    directory.save(codebase)
    return directory


def compute_directory_fingerprints(directory, codebase):
    """
    Recursivly compute fingerprints for `directory` from `codebase`
    """
    for resource in directory.walk(codebase, topdown=False):
        if resource.is_file:
            continue
        _ = _compute_directory_fingerprints(resource, codebase)
    return directory


def compute_codebase_directory_fingerprints(codebase):
    """
    Compute fingerprints for directories from `codebase`
    """
    for resource in codebase.walk(topdown=False):
        if resource.is_file or not resource.path:
            continue
        _ = _compute_directory_fingerprints(resource, codebase)
    return codebase


def split_fingerprint(directory_fingerprint):
    """
    Given a string `directory_fingerprint`, return the indexed elements count as
    an integer and the bah128 fingerprint string
    """
    indexed_elements_count_hash = directory_fingerprint[0:8]
    indexed_elements_count = int(indexed_elements_count_hash, 16)
    bah128 = directory_fingerprint[8:]
    return indexed_elements_count, bah128


def hexstring_to_binarray(hex_string):
    """
    Convert a hex string to binary form, then store in a bytearray
    """
    return bytearray(binascii.unhexlify(hex_string))


def create_halohash_chunks(bah128):
    """
    Given a 128-bit bah128 hash string, split it into 4 chunks and return those
    chunks as bytearrays
    """
    chunk1 = bah128[0:8]
    chunk2 = bah128[8:16]
    chunk3 = bah128[16:24]
    chunk4 = bah128[24:32]

    chunk1 = hexstring_to_binarray(chunk1)
    chunk2 = hexstring_to_binarray(chunk2)
    chunk3 = hexstring_to_binarray(chunk3)
    chunk4 = hexstring_to_binarray(chunk4)

    return chunk1, chunk2, chunk3, chunk4


def select_windows(windows):
    """
    Return an iterable of selected windows using the hailstorm algorithm. A
    window is a list of ngram bytestrings.

    Definition from the paper: http://www2009.eprints.org/7/1/p61.pdf

      The algorithm first fingerprints every token and then selects a shingle s
      if the minimum fingerprint value of all k tokens in s occurs at the first
      or the last position of s (and potentially also in between). Due to the
      probabilistic properties of Rabin fingerprints the probability that a
      shingle is chosen is 2/k if all tokens in the shingle are different
    """
    last = None
    window = None
    for pos, window in enumerate(windows):
        nghs = []
        for ngram in window:
            # TODO: use different algorithm?
            nghs.append(binascii.crc32(ngram) & 0xffffffff)
        min_hash = min(nghs)
        if min_hash in (nghs[0], nghs[-1]):
            yield window
            last = window
        else:
            # always yield the first or last window too.
            if pos == 0:
                yield window
                last = window
    if last != window:
        yield window


# Split on whitespace and punctuations: keep only characters and numbers
query_pattern = '[^_\\W]+'
word_splitter = re.compile(query_pattern, re.UNICODE).findall


def _tokenizer(text):
    """
    Return an list of tokens from a unicode text.
    """
    if not text:
        return []
    return [token for token in word_splitter(text) if token]


def tokenizer(text):
    """
    Return an list of tokens from a unicode text.

    For example::
    >>> list(tokenizer(''))
    []
    >>> x = list(tokenizer('some Text with   spAces! + _ -'))
    >>> assert x == ['some', 'text', 'with', 'spaces']

    >>> x = list(tokenizer('{{}some }}Text with   spAces! + _ -'))
    >>> assert x == ['some', 'text', 'with', 'spaces']

    >>> x = list(tokenizer('{{Hi}}some {{}}Text with{{noth+-_!@ing}}   {{junk}}spAces! + _ -{{}}'))
    >>> assert x == ['hi', 'some', 'text', 'with', 'noth', 'ing', 'junk', 'spaces']

    """
    return _tokenizer(text.lower())


def get_file_fingerprint_hashes(location, **kwargs):
    """
    Return a mapping of fingerprint hashes for the file at `location`

    The `halo1` hash is the hex digest of the fingerprint of the file.
    `halo1` is empty if the file is empty.

    'chunks_halo1` is a list of fingerprints foreach chunk in the file at
    `location`

    - We start by breaking the file into words (tokens)
    - We compute ngrams over the list of tokens
    - The list of ngrams is then broken into chunks
    - We compute a fingerprint for each chunk

    Return an empty list if `location` is not a text file
    """
    # TODO: Make these values global
    ngram_length = 8
    # window_length is the sliding window we have over the ngrams list
    window_length = 64

    # Do not process `location` if it's not a text file
    if not filetype.is_file(location):
        return {}
    ft = get_type(location)
    if not ft.is_text:
        return {}

    # TODO: Check for robust text-reading code in license and copyright detection
    with codecs.open(location, encoding='utf-8') as f:
        content = f.read()

    # break content into words, then create ngrams from words
    words = tokenizer(content)
    ngs = ngrams(words, ngram_length)
    # We convert each list of ngrams to a sequence of bytes
    ngs = [' '.join(ng).encode('utf-8') for ng in ngs]

    # compute and select sliding windows on ngrams
    windows = ngrams(ngs, window_length)
    selected_windows = select_windows(windows)

    # Create fingerprints and return fingerprint hashes
    file_fingerprint = BitAverageHaloHash(ngs) if ngs else None
    chunk_fingerprints = [BitAverageHaloHash(window) for window in selected_windows]
    return dict(
        halo1=file_fingerprint.hexdigest().decode('utf-8') if file_fingerprint else '',
        chunks_halo1=[chunk_fingerprint.hexdigest().decode('utf-8') for chunk_fingerprint in chunk_fingerprints]
    )
