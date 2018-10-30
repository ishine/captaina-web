import pymodm as modm
import pymongo as mongo
import pathlib
from .user import User
from .lesson import Lesson, Prompt
import itsdangerous
import bson.json_util
from datetime import datetime
import json


class AudioRecord(modm.MongoModel):
    user = modm.fields.ReferenceField(User)
    prompt = modm.fields.ReferenceField(Prompt)
    filekey = modm.fields.CharField()
    passed_validation = modm.fields.BooleanField()
    created = modm.fields.DateTimeField(default = datetime.now)
    modified  = modm.fields.DateTimeField(default = datetime.now)

    def save(self, *args, **kwargs):
        self.modified = datetime.now()
        super().save(*args, **kwargs)

    class Meta:
        indexes = [mongo.operations.IndexModel([('filekey',mongo.ASCENDING)], unique = True)]

def validate_audio_record_files(audio_record, audio_store_path):
    #Makes sure the audio_record's referenced files are found.
    audio_store = pathlib.Path(audio_store_path)
    id_path = audio_store / audio_record.filekey
    audio_file_path = id_path.with_suffix(".raw")
    align_file_path = id_path.with_suffix(".ali.json") 
    return audio_file_path.exists() and align_file_path.exists()

def choose_word_alignments(word_alignment):
    """ Chooses the last occurences of each word in the alignment,
    and nothing more """
    chosen = {}
    for word_dict in word_alignment:    
        word = word_dict["word"]
        if word == "<UNK>" or "[TRUNC:]" in word:
            continue
        word_index = int(word.split("@")[1])
        chosen[word_index] = word_dict
    return [chosen[index] for index in sorted(chosen.keys())]

def fetch_word_alignment(audio_record, audio_store_path):
    audio_store = pathlib.Path(audio_store_path)
    id_path = audio_store / audio_record.filekey
    align_file_path = id_path.with_suffix(".ali.json") 
    return json.loads(align_file_path.read_text())["word-alignment"]

class LessonRecord(modm.MongoModel):
    user = modm.fields.ReferenceField(User)
    lesson = modm.fields.ReferenceField(Lesson)
    sequence_id = modm.fields.IntegerField()
    audio_records = modm.fields.ListField(modm.fields.ReferenceField(AudioRecord),
            blank = True, default = list)
    created = modm.fields.DateTimeField(default = datetime.now)
    modified  = modm.fields.DateTimeField(default = datetime.now)

    def save(self, *args, **kwargs):
        self.modified = datetime.now()
        super().save(*args, **kwargs)

    class Meta:
        #Each record is uniquely identified by the user, lesson and sequence id combination
        indexes = [mongo.operations.IndexModel([
            ('user', mongo.ASCENDING),
            ('lesson', mongo.ASCENDING),
            ('sequence_id', mongo.DESCENDING)], unique = True)] 

    def get_id(self):
        return bson.json_util.dumps(self._id)

    def is_complete(self):
        #A bit convoluted, but:
        #Make sure there is at least one audio_record which passed validation
        # for each prompt
        for prompt in self.lesson.prompts:
            if not any(record.prompt == prompt and record.passed_validation
                    for record in self.audio_records):
                return False
        return True

    def num_prompts_completed(self):
        for i, prompt in enumerate(self.lesson.prompts):
            if not any(record.prompt == prompt and record.passed_validation
                    for record in self.audio_records):
                return i
        return len(self.lesson.prompts)
    
    def validated_audio_records(self):
        return [record for record in self.audio_records if record.passed_validation]

    def reviews_exist(self):
        from .review import AudioReview
        validated_records = self.validated_audio_records()
        if not validated_records:
            return False
        for audio_record in validated_records:
            try:
                AudioReview.objects.get({"audio_record": audio_record.pk})
            except AudioReview.DoesNotExist:
                return False
        return True

class ReferenceRecord(modm.MongoModel):
    user = modm.fields.ReferenceField(User)
    lesson = modm.fields.ReferenceField(Lesson)
    audio_records = modm.fields.ListField(modm.fields.ReferenceField(AudioRecord),
            blank = True, default = list)
    created = modm.fields.DateTimeField(default = datetime.now)
    modified  = modm.fields.DateTimeField(default = datetime.now)

    def save(self, *args, **kwargs):
        self.modified = datetime.now()
        super().save(*args, **kwargs)

    def get_id(self):
        return bson.json_util.dumps(self._id)

    def get_reference(self, prompt):
        possible_references = [record for record in self.audio_records if
                record.passed_validation and record.prompt == prompt]
        if not possible_references:
            return None
        else:
            #Current decision: return the latest one
            return possible_references[-1]

    def reference_exists(self, prompt):
        if any(record for record in self.audio_records if
                record.passed_validation and record.prompt == prompt):
            return True
        else:
            return False

    class Meta:
        #Only one reference per lesson, per user
        indexes = [mongo.operations.IndexModel([
            ('user', mongo.ASCENDING),
            ('lesson', mongo.ASCENDING)], unique = True)] 

def get_or_make_reference_record(user, lesson):
    try:
        return ReferenceRecord.objects.raw({'user':user.pk, 'lesson':lesson.pk}).first()
    except ReferenceRecord.DoesNotExist:
        record = ReferenceRecord(user = user.pk, 
                lesson = lesson.pk)
        record.save(force_insert = True)
        return record
    except mongo.errors.DuplicateKeyError: #Duplicate request
        raise ValueError("Duplicate request")

def load_record(record_id):
    try:
        return LessonRecord.objects.get({"_id": bson.json_util.loads(record_id)})
    except LessonRecord.DoesNotExist:
        try:
            return ReferenceRecord.objects.get({"_id": bson.json_util.loads(record_id)})
        except ReferenceRecord.DoesNotExist:
            raise ValueError("Record does not exist")

def cookie_from_record(record, secret_key):
    s = itsdangerous.URLSafeTimedSerializer(secret_key)
    return s.dumps(record.get_id())

def record_from_cookie(cookie, secret_key, max_age = 3600): 
    # Raises ValueError if not found
    s = itsdangerous.URLSafeTimedSerializer(secret_key)
    record_id = s.loads(cookie, max_age = max_age)
    return load_record(record_id)

#TODO: refactor all to use cookie_from_record and record_from_cookie
def load_lesson_record(record_id):
    # Raises LessonRecord.DoesNotExist if not found
    return LessonRecord.objects.get({"_id": bson.json_util.loads(record_id)})
def cookie_from_lesson_record(lesson_record, secret_key):
    s = itsdangerous.URLSafeTimedSerializer(secret_key)
    return s.dumps(lesson_record.get_id())
def lesson_record_from_cookie(cookie, secret_key, max_age = 3600): 
    # Raises LessonRecord.DoesNotExist if not found
    s = itsdangerous.URLSafeTimedSerializer(secret_key)
    record_id = s.loads(cookie, max_age = max_age)
    return load_lesson_record(record_id)

def get_latest_lesson_record(user, lesson):
    #raises LessonRecord.DoesNotExist if not found
    records = LessonRecord.objects.raw({'user':user.pk, 'lesson':lesson.pk})
    if records.count() == 0:
        raise LessonRecord.DoesNotExist()
    result = records.order_by([('sequence_id', mongo.DESCENDING)]).first()
    if result is None:
        raise LessonRecord.DoesNotExist()
    return result

def ensure_and_get_latest_lesson_record(user, lesson):
    try:
        return get_latest_lesson_record(user,lesson)
    except LessonRecord.DoesNotExist:
        record = LessonRecord(user = user.pk, 
                lesson = lesson.pk,
                sequence_id = 1)
        record.save(force_insert = True)
        return record
    except mongo.errors.DuplicateKeyError: #Duplicate request
        raise ValueError("Duplicate request")

def ensure_and_get_incomplete_lesson_record(user, lesson):
    #Fetches or creates an incomplete lesson record for the user, lesson combination
    #If there is a duplicate request and the record cannot be created, raises ValueError
    old_record = ensure_and_get_latest_lesson_record(user, lesson)
    if not old_record.is_complete():
        return old_record
    else:
        try:
            new_record = LessonRecord(user = user.pk, 
                    lesson = lesson.pk,
                    sequence_id = old_record.sequence_id + 1)
            new_record.save(force_insert = True) 
            return new_record
        except mongo.errors.DuplicateKeyError: #Duplicate request
            raise ValueError("Duplicate request")
