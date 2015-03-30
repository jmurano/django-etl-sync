from __future__ import print_function
import os
import warnings
import unicodecsv as csv
from datetime import datetime
from copy import copy
from django.core.exceptions import ValidationError
from django.db import IntegrityError, DatabaseError
from django.conf import settings
from etl_sync.generators import InstanceGenerator
from etl_sync.transformations import Transformer


class FeedbackCounter(object):
    """Keeps track of the etl process and provides feedback."""

    def __init__(self, message=None, feedbacksize=5000, counter=0):
        self.counter = counter
        self.feedbacksize = feedbacksize
        self.message = message
        self.rejected = 0
        self.created = 0
        self.updated = 0
        self.child_rows = 0
        self.starttime = datetime.now()
        self.feedbacktime = self.starttime

    def _feedback(self):
        """Print feedback."""
        if self.counter % self.feedbacksize == 0:
            print(
                '{0} {1} processed in {2}, {3}, {4} created, {5} updated, '
                '{6} rejected, {7} child rows'.format(
                    self.message,
                    self.feedbacksize,
                    datetime.now()-self.feedbacktime,
                    self.counter,
                    self.created,
                    self.updated,
                    self.rejected,
                    self.child_rows,
                )
            )
            self.feedbacktime = datetime.now()

    def increment(self):
        if self.counter > 0:
            self._feedback()
        self.counter += 1

    def reject(self):
        self.rejected += 1
        self.increment()

    def inc_child_rows(self):
        self.child_rows += 1
        self.increment()

    def create(self):
        #   20141105 jmurano
        #   moved to use_result()
        # self.increment()
        self.created += 1

    def update(self):
        # self.increment()
        self.updated += 1

    def use_result(self, res):
        """Use feedback from InstanceGenerator to set counters."""
        self.increment()
        if res.get('created'):
            self.create()
        elif res.get('updated'):
            self.update()


    def finished(self):
        """Provides final message."""
        return (
            'Data extraction finished {0}\n\n{1} '
            'created\n{2} updated\n{3} rejected\n{4} child rows'.format(
                datetime.now(),
                self.created,
                self.updated,
                self.rejected,
                self.child_rows,
            )
        )


class FileReaderLogManager():
    """Context manager that creates the reader and handles files."""

    def __init__(
        self,
        filename,
        logname=None,
        reader_class=None,
        encoding=None,
        reject_filename=None,
    ):
        self.filename = filename
        self.log = logname
        self.reader = reader_class
        self.encoding = encoding
        self.file = None
        self.logfile = None
        self.reject_filename = reject_filename
        self.reject_file = None
        self.reject_writer = None

    def _log(self, text):
        """Log to logfile or to stdout if self.logfile=None"""
        print(text, file=self.logfile)

    def _reject(self, csv_row):
        """Record rejected row"""
        self.reject_writer.writerows([csv_row])

    def __enter__(self):
        self.file = open(self.filename, 'r')
        self.logfile = open(self.log, 'w')
        #   'wb' makes it also work on Windows machines
        self.reject_file = open(self.reject_filename, 'wb')
        if self.reader:
            reader = self.reader(self.file)
        else:
            reader = csv.DictReader(
                self.file,
                delimiter='\t',
                quoting=csv.QUOTE_NONE
            )
        reader.log = self._log
        self.reject_writer = csv.DictWriter(
            self.reject_file,
            reader.fieldnames + ['rejection_reason'],
            encoding='latin-1',
        )
        self.reject_writer.writeheader()
        reader.reject = self._reject
        return reader

    def __exit__(self, type, value, traceback):
        for filehandle in [self.file, self.logfile, self.reject_file]:
            try:
                filehandle.close()
            except (AttributeError, IOError):
                pass


class Mapper(object):
    """Generic mapper object for ETL. Create reader_class for file formats others
    than tab-delimited CSV."""
    reader_class = None
    transformer_class = Transformer
    model_class = None
    filename = None
    encoding = 'utf-8'
    slice_begin = None
    slice_end = None
    defaults = {}
    create_new = True
    update = True
    create_foreign_key = True
    etl_persistence = ['record']
    message = 'Data Extraction'
    result = None
    feedbacksize = 5000
    logfile = None
    logfilename = None
    forms = []
    counter = None
    reject_filename = None
    #   20140218 jmurano
    #   date_flexibility is used by some CocoaAction transformers
    #   to allow records with duplicate dates that would otherwise be rejected
    date_flexibility = False

    def __init__(self, *args, **kwargs):
        for k in kwargs:
            if hasattr(self, k):
                setattr(self, k, kwargs[k])
            else:
                warnings.warn(
                    'Invalid keyword argument for Mapper will be ignored.')
        if not self.encoding:
            self.encoding = 'utf-8'
        if hasattr(settings, 'ETL_FEEDBACK'):
            self.feedbacksize = settings.ETL_FEEDBACK
        if self.filename:
            self.logfilename = os.path.join(
                os.path.dirname(self.filename),
                '{0}.{1}.log'.format(
                    self.filename,
                    datetime.now().strftime('%Y-%m-%d')
                )
            )

    def _log(self, text):
        """Log to logfile or to stdout if self.logfile=None"""
        print(text, file=self.logfile)

    def load(self):
        """Loads data into database using Django models and error logging."""
        print('Opening {0} using {1}'.format(self.filename, self.encoding))
        reject_filename = self.filename
        if reject_filename[-4:] == '.csv':
            reject_filename = reject_filename[:-4]
        reject_filename += '-rejected.csv'
        self.reject_filename = reject_filename
        with FileReaderLogManager(
            self.filename,
            logname=self.logfilename,
            reader_class=self.reader_class,
            encoding=self.encoding,
            reject_filename=reject_filename,
        ) as reader:
            reader.log(
                'Data extraction started {0}\n\nStart line: '
                '{1}\nEnd line: {2}\n'.format(
                    datetime.now().strftime('%Y-%m-%d'),
                    self.slice_begin,
                    self.slice_end
                )
            )
            self.counter = FeedbackCounter(
                feedbacksize=self.feedbacksize,
                message=self.message
            )
            while self.slice_begin and self.slice_begin > self.counter.counter:
                reader.next()
                self.counter.increment()
            while not self.slice_end or self.slice_end >= self.counter.counter:
                save_record = True
                error_msg = None
                try:
                    csv_dic = reader.next()
                    csv_copy = copy(csv_dic)
                except (UnicodeDecodeError, csv.Error):
                    error_msg = 'Text decoding or CSV error'
                    save_record = False
                except StopIteration:
                    reader.log('End of file.')
                    break
                transformer = self.transformer_class(
                    csv_dic,
                    self.defaults,
                    self.date_flexibility,
                )
                if transformer.is_valid():
                    dic = transformer.cleaned_data
                elif transformer.is_child_row and not transformer.error:
                    self.counter.inc_child_rows()
                    save_record = False
                else:
                    error_msg = 'Validation error (%s)' % transformer.error
                    save_record = False
                if save_record:
                    # remove keywords conflicting with Django model
                    # TODO: I think that is done in several places now
                    # determine the correct one and get rid of the others
                    if 'id' in dic:
                        del dic['id']
                    generator = InstanceGenerator(
                        self.model_class,
                        dic,
                        persistence=self.etl_persistence
                    )
                    try:
                        generator.get_instance()
                    except ValidationError, e:
                        error_msg = 'ValidationError: %s' % e.message
                    except (IntegrityError, DatabaseError), e:
                        error_msg = 'Error (%s)' % str(e)
                    else:
                        self.counter.use_result(generator.res)
                if error_msg:
                    reader.log('%s in line %d => rejected' % (
                        error_msg,
                        self.counter.counter,
                    ))
                    self.counter.reject()
                    csv_copy['rejection_reason'] = error_msg
                    reader.reject(csv_copy)
            reader.log(self.counter.finished())
        self.result = 'loaded'
