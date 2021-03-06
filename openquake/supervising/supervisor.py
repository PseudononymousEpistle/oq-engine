# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010-2012, GEM Foundation.
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.


"""
This module implements supervise(), a function that monitors an OpenQuake job.

Upon job termination, successful or for an error of whatever level or gravity,
supervise() will:

   - ensure a cleanup of the resources used by the job
   - update status of the job record in the database
"""

import logging
import os
import signal
from datetime import datetime

try:
    # setproctitle is optional external dependency
    # apt-get installl python-setproctitle or
    # http://pypi.python.org/pypi/setproctitle/
    from setproctitle import setproctitle
except ImportError:
    setproctitle = lambda title: None  # pylint: disable=C0103

from openquake.db.models import OqJob, ErrorMsg, JobStats
from openquake import supervising
from openquake import kvs
from openquake import logs
from openquake.utils import monitor
from openquake.utils import stats


LOG_FORMAT = ('[%(asctime)s #%(job_id)s %(hostname)s %(levelname)s '
              '%(processName)s/%(process)s %(name)s] %(message)s')


def ignore_sigint():
    """
    Setup signal handler on SIGINT in order to ignore it.

    This is needed to avoid premature death of the supervisor and is called
    from :func:`openquake.engine.run_job` for job parent process and
    from :func:`supervise` for supervisor process.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def terminate_job(pid):
    """
    Terminate an openquake job by killing its process.

    :param pid: the process id
    :type pid: int
    """

    logging.info('Terminating job process %s', pid)

    os.kill(pid, signal.SIGKILL)


def record_job_stop_time(job_id):
    """
    Call this when a job concludes (successful or not) to record the
    'stop_time' (using the current UTC time) in the uiapi.job_stats table.

    :param job_id: the job id
    :type job_id: int
    """
    logging.info('Recording stop time for job %s to job_stats', job_id)

    job_stats = JobStats.objects.get(oq_job=job_id)
    job_stats.stop_time = datetime.utcnow()
    job_stats.save(using='job_superv')


def cleanup_after_job(job_id):
    """
    Release the resources used by an openquake job.

    :param job_id: the job id
    :type job_id: int
    """
    logging.info('Cleaning up after job %s', job_id)

    kvs.cache_gc(job_id)


def get_job_status(job_id):
    """
    Return the status of the job stored in its database record.

    :param job_id: the id of the job
    :type job_id: int
    :return: the status of the job
    :rtype: string
    """

    return OqJob.objects.get(id=job_id).status


def update_job_status_and_error_msg(job_id, status, error_msg=None):
    """
    Store in the database the status of a job and optionally an error message.

    :param job_id: the id of the job
    :type job_id: int
    :param status: the status of the job, e.g. 'failed'
    :type status: string
    :param error_msg: the error message, if any
    :type error_msg: string or None
    """
    job = OqJob.objects.get(id=job_id)
    job.status = status
    job.save()

    if error_msg:
        ErrorMsg.objects.using('job_superv')\
                        .create(oq_job=job, detailed=error_msg)


def _update_log_record(self, record):
    """
    Massage a log record before emitting it. Intended to be used by the
    custom log handlers defined in this module.
    """
    if not hasattr(record, 'hostname'):
        record.hostname = '-'
    if not hasattr(record, 'job_id'):
        record.job_id = self.job_id
    logger_name_prefix = 'oq.job.%s' % record.job_id
    if record.name.startswith(logger_name_prefix):
        record.name = record.name[len(logger_name_prefix):].lstrip('.')
        if not record.name:
            record.name = 'root'


class SupervisorLogStreamHandler(logging.StreamHandler):
    """
    Log stream handler intended to be used with
    :class:`SupervisorLogMessageConsumer`.
    """

    def __init__(self, job_id):
        super(SupervisorLogStreamHandler, self).__init__()
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self.job_id = job_id

    def emit(self, record):  # pylint: disable=E0202
        _update_log_record(self, record)
        super(SupervisorLogStreamHandler, self).emit(record)


class SupervisorLogFileHandler(logging.FileHandler):
    """
    Log file handler intended to be used with
    :class:`SupervisorLogMessageConsumer`.
    """

    def __init__(self, job_id, log_file):
        super(SupervisorLogFileHandler, self).__init__(log_file)
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self.job_id = job_id
        self.log_file = log_file

    def emit(self, record):  # pylint: disable=E0202
        _update_log_record(self, record)
        super(SupervisorLogFileHandler, self).emit(record)


def abort_due_to_failed_nodes(job_id):
    """Should the job be aborted due to failed compute nodes?

    The job should be aborted when the following conditions coincide:
        - we observed failed compute nodes
        - the "no progress" timeout has been exceeded

    :param int job_id: the id of the job in question
    :returns: the number of failed compute nodes if the job should be aborted
        zero otherwise.
    """
    logging.debug("> abort_due_to_failed_nodes")
    result = 0

    job = OqJob.objects.get(id=job_id)
    failed_nodes = monitor.count_failed_nodes(job)
    logging.debug(">> failed_nodes: %s" % failed_nodes)

    if failed_nodes:
        no_progress_period, timeout = stats.get_progress_timing_data(job)
        logging.debug(">> no_progress_period: %s" % no_progress_period)
        logging.debug(">> timeout: %s" % timeout)
        if no_progress_period > timeout:
            result = failed_nodes

    logging.debug("< abort_due_to_failed_nodes")
    return result


class SupervisorLogMessageConsumer(logs.AMQPLogSource):
    """
    Supervise an OpenQuake job by:

       - handling its "critical" and "error" messages
       - periodically checking that the job process is still running
    """
    # Failure counter check delay, translates to 60 seconds with the current
    # settings.
    FCC_DELAY = 60

    def __init__(self, job_id, job_pid, timeout=1):
        self.selflogger = logging.getLogger('oq.job.%s.supervisor' % job_id)
        self.selflogger.info('Entering supervisor for job %s', job_id)
        logger_name = 'oq.job.%s' % job_id
        key = '%s.#' % logger_name
        super(SupervisorLogMessageConsumer, self).__init__(timeout=timeout,
                                                           routing_key=key)
        self.job_id = job_id
        self.job_pid = job_pid
        self.joblogger = logging.getLogger(logger_name)
        self.jobhandler = logging.Handler(logging.ERROR)
        self.jobhandler.emit = self.log_callback
        self.joblogger.addHandler(self.jobhandler)
        # Failure counter check delay value
        self.fcc_delay_value = 0

    def run(self):
        """
        Wrap superclass' method just to add cleanup.
        """
        started = datetime.utcnow()
        super(SupervisorLogMessageConsumer, self).run()
        stopped = datetime.utcnow()
        self.selflogger.info('Job %s finished in %s',
                             self.job_id, stopped - started)
        self.joblogger.removeHandler(self.jobhandler)
        self.selflogger.info('Exiting supervisor for job %s', self.job_id)

    def log_callback(self, record):
        """
        Handles messages of severe level from the supervised job.
        """
        if record.name == self.selflogger.name:
            # ignore error log messages sent by selflogger.
            # this way we don't try to kill the job if its
            # process has crashed (or has been stopped).
            # we emit selflogger's error messages from
            # timeout_callback().
            return

        terminate_job(self.job_pid)

        update_job_status_and_error_msg(self.job_id, 'failed',
                                        record.getMessage())

        record_job_stop_time(self.job_id)

        cleanup_after_job(self.job_id)

        self.stop()

    def timeout_callback(self):
        """
        On timeout expiration check if the job process is still running
        and whether it experienced any failures.

        Terminate the job process in the latter case.
        """
        def failure_counters_need_check():
            """Return `True` if failure counters should be checked."""
            self.fcc_delay_value += 1
            result = self.fcc_delay_value >= self.FCC_DELAY
            if result:
                self.fcc_delay_value = 0
            return result

        process_stopped = job_failed = False
        message = None

        if not supervising.is_pid_running(self.job_pid):
            message = ('job process %s crashed or terminated' % self.job_pid)
            process_stopped = True
        elif failure_counters_need_check():
            # Job process is still running.
            failures = stats.failure_counters(self.job_id)
            if failures:
                message = "job terminated with failures: %s" % failures
            else:
                failed_nodes = abort_due_to_failed_nodes(self.job_id)
                if failed_nodes:
                    message = ("job terminated due to %s failed nodes" %
                               failed_nodes)
            if failures or failed_nodes:
                terminate_job(self.job_pid)
                job_failed = True

        if job_failed or process_stopped:
            job_status = get_job_status(self.job_id)
            if process_stopped and job_status == 'succeeded':
                message = 'job process %s succeeded' % self.job_pid
                self.selflogger.info(message)
            elif job_status == 'running':
                # The job crashed without having a chance to update the
                # status in the database, or it has been running even though
                # there were failures. We update the job status here.
                self.selflogger.error(message)
                update_job_status_and_error_msg(self.job_id, 'failed', message)

            record_job_stop_time(self.job_id)
            cleanup_after_job(self.job_id)
            raise StopIteration()


def supervise(pid, job_id, timeout=1, log_file=None):
    """
    Supervise a job process, entering a loop that ends only when the job
    terminates.

    :param pid: the process id
    :type pid: int
    :param job_id: the job id
    :type job_id: int
    :param timeout: timeout value in seconds
    :type timeout: float
    :param str log_file:
        Optional log file location. If specified, log messages will be appended
        to this file. If not specified, log messages will be printed to the
        console.
    """
    # Set the name of this process (as reported by /bin/ps)
    setproctitle('openquake supervisor for job_id=%s job_pid=%s'
                 % (job_id, pid))
    ignore_sigint()

    if log_file is not None:
        logging.root.addHandler(SupervisorLogFileHandler(job_id, log_file))
    else:
        logging.root.addHandler(SupervisorLogStreamHandler(job_id))

    supervisor = SupervisorLogMessageConsumer(job_id, pid, timeout)
    supervisor.run()
