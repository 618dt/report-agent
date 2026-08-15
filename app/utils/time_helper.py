"""
    time_helper.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    时间处理工具类

    

    :author: lcg
    :date created: 2026/8/1

"""
from calendar import timegm
from datetime import datetime


def utc2local(utc_dt):
    """
    Convert utc datetime to local datetime

    """
    return datetime.fromtimestamp(timegm(utc_dt.timetuple()))


def datetime2timestamp(dt):
    return int(timegm(dt.timetuple()) * 1000 + dt.microsecond / 1e3)


def datetime2timestamp_10(dt):
    """datetime转10位时间戳"""
    return int(dt.timestamp())


def timestamp2datetime(stamp, to_local=False):
    dt = datetime.utcfromtimestamp(stamp / 1e3)
    if to_local:
        return utc2local(dt)
    return dt
