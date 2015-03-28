from django.core.exceptions import ValidationError


#   20141105 jmurano
#   adding a "child row" flag
class Transformer(object):
    """Base transformer. Django forms can be used instead.
    This class contains only the bare minimum of methods
    and is able to process a list of forms."""
    forms = []
    error = None
    # dictionary of mappings applied in remap
    mappings = {}
    is_child_row = False
    #   20140218 jmurano
    #   date_flexibility is used by some CocoaAction transformers
    #   to allow records with duplicate dates that would otherwise be rejected
    date_flexibility = False

    def __init__(self, dic, defaults={}, date_flexibility=False):
        self.dic = dic
        self.defaults = defaults
        self.date_flexibility = date_flexibility

    def _process_forms(self, dic):
        """Processes a list of forms."""
        for form in self.forms:
            frm = form(dic)
            if frm.is_valid():
                dic.update(frm.cleaned_data)
            else:
                for error in frm.errors['__all__']:
                    raise ValidationError(error)
        return dic

    def _apply_defaults(self, dictionary):
        """Adds defaults to the dictionary."""
        if type(self.defaults) is dict:
            dic = self.defaults.copy()
        else:
            dic = {}
        dic = dict(dic.items() + dictionary.items())
        return dic

    def validate(self, dic):
        """Raise validation errors here."""
        pass

    def remap(self, dic):
        """Use this method for remapping dictionary keys."""
        for key in self.mappings:
            dic[self.mappings[key]] = dic[key]
            del dic[key]
        return dic

    def transform(self, dic):
        """Additional transformations not covered by remap and forms."""
        return dic

    def full_transform(self, dic):
        """Runs all four transformation steps."""
        # Order is important here
        dic = self.remap(dic)
        dic = self._apply_defaults(dic)
        dic = self._process_forms(dic)
        dic = self.transform(dic)
        self.validate(dic)
        return dic

    def clean(self, dic):
        """For compatibility with Django's form class."""
        return self.full_transform(dic)

    def is_valid(self):
        try:
            self.cleaned_data = self.clean(self.dic)
            if self.is_child_row:
                return False
            return True
        except KeyError, e:
            self.error = 'KeyError (missing field): %s' % e
            return False
        except ValueError, e:
            self.error = 'ValueError: %s' % e
            return False
        except (ValidationError, UnicodeEncodeError), e:
            self.error = e
            return False
