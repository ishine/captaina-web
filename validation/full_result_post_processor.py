#!/usr/bin/env python3
#This file copies parts of:
#https://github.com/alumae/kaldi-gstreamer-server/blob/master/sample_full_post_processor.py
import tornado.httpclient
import json
import logging
import pathlib
import sys
http_client = tornado.httpclient.HTTPClient()
GRAPH_DIR = pathlib.Path(".")
AUDIO_DIR = pathlib.Path(".")

def validate(hyp, ref, max_miscues=3):
    #Make sure all reference words were found:
    if not all(word in hyp for word in ref):
        return False
    #When all reference words are found, the number of miscues
    #is simply:
    num_miscues = len(hyp) - len(ref)
    if num_miscues > max_miscues:
        return False
    #Otherwise, all good!
    return True

def get_ref(graph_id):
    prompt_path = GRAPH_DIR / graph_id / "uniqued_prompt.txt"
    with prompt_path.open(encoding="utf8") as fi:
        return fi.read().strip().split()

def save_alignment(wav_key, alignment):
    alignment_key = wav_key + ".ali.json"
    alignment_path = AUDIO_DIR / alignment_key 
    with alignment_path.open("w", encoding="utf8") as fo:
        fo.write(json.dumps(alignment))

def post_result_to_backend(record_cookie, graph_id, wav_key, verdict):
    data = json.dumps({"record-cookie": record_cookie, 
        "graph-id": graph_id,
        "file-key": wav_key,
        "passed-validation": verdict})
    response = http_client.fetch("http://web/api/log-audio", 
            headers = {"content-type": "application/json"},
            method = 'POST',
            body = data)
    if response.body == b"OK":
        logging.info("{id}: Result posted to backend".format(id=wav_key))
    else:
        logging.info("{id}: Saving to backend failed, message: {msg}".format(
            id=wav_key,
            msg=response.body))

def parse(asr_output):
    try:
        data = json.loads(asr_output)
    except: #From 
        exc_type, exc_value, exc_traceback = sys.exc_info()
        logging.error("Failed to process JSON result: %s : %s " % (exc_type, exc_value))
        raise ValueError()
    best_hyp = data["result"]["hypotheses"][0]["transcript"].split()
    graph_id = data["graph-id"]
    record_cookie = data["record-cookie"]
    wav_key = data["id"]
    alignment = data["result"]["hypotheses"][0]["phone-alignment"]
    return {"hyp": best_hyp, 
            "graph_id": graph_id, 
            "record_cookie": record_cookie, 
            "wav_key": wav_key, 
            "alignment": alignment}, data

def write_output(result):
    print(result)
    print() #Empty line signifies end, I think, at least used in alumae's sample

def do_post_processing(asr_output):
    try:
        parsed, data = parse(asr_output)
    except ValueError():
        write_output(json.dumps({"status": "error"}))
        return
    ref = get_ref(parsed["graph_id"])
    logging.info("{id}: Validating (hyp vs. ref): {hyp} vs. {ref}".format(
        id=parsed["wav_key"], 
        hyp = parsed["hyp"], 
        ref = ref))
    validation_verdict = validate(hyp=parsed["hyp"], ref=ref)
    logging.info("{id}: Validation verdict: {verdict}".format(
        id=parsed["wav_key"], 
        verdict = "Accepted" if validation_verdict else "Rejected"))
    save_alignment(parsed["wav_key"], parsed["alignment"])
    post_result_to_backend(record_cookie = parsed["record_cookie"], 
            graph_id = parsed["graph_id"], 
            wav_key = parsed["wav_key"], 
            verdict = validation_verdict)
    data["validition-result"] = validation_verdict
    return json.dumps(data)
        
def main_loop():
    #Structure from alumae's sample
    lines = []
    while True:
        l = sys.stdin.readline()
        if not l: break # EOF
        if l.strip() == "":
            if len(lines) > 0:
                result_json = do_post_processing("".join(lines))
                write_output(result_json)
                sys.stdout.flush()
                lines = []
        else:
            lines.append(l)
    if len(lines) > 0:
        result_json = do_post_processing("".join(lines))
        print(result_json)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser("Validates kaldi-gstreamer-server output for miscue-tolerant decoding")
    parser.add_argument("graphdir", help = "Directory to search for graph related material")
    parser.add_argument("audiodir", help = "Directory where uploaded audio sits. This is where the alignment is saved")
    args = parser.parse_args()
    GRAPH_DIR = pathlib.Path(args.graphdir)
    AUDIO_DIR = pathlib.Path(args.audiodir)
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)8s %(asctime)s %(message)s ")
    main_loop()
