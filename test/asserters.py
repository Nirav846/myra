"""
    The MIT License (MIT)

    Copyright (c) 2023 pkjmesra

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""
from pprint import pformat


def assert_calls_equal(expected, actual):
    """
    Check whether the given mock object (or mock method) calls are equal and
    return a nicely formatted message.
    """
    if not expected == actual:
        raise_calls_differ_error(expected, actual)


def raise_calls_differ_error(expected, actual):
    """
    Raise an AssertionError with pretty print format for the given expected
    and actual mock calls in order to ensure consistent print style for better
    readability.
    """
    expected_str = pformat(expected)
    actual_str = pformat(actual)
    msg = '\nMock calls differ!\nExpected calls:\n{}\nActual calls:\n{}'.format(
        expected_str,
        actual_str
    )
    raise AssertionError(msg)


def assert_calls_equal_unsorted(expected, actual):
    """
    Raises an AssertionError if the two iterables do not contain the same items.

    The order of the items is ignored
    """
    for expected in expected:
        if expected not in actual:
            raise_calls_differ_error(expected, actual)
