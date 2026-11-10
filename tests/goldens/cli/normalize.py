"""What two runs of a command can differ in, made the same: pids, a call's cost, and where things are."""
import re

def normalize(text, places):
    for name, path in places.items():
        text = text.replace(path, '{' + name + '}')
    text = re.sub(r'"pid":\d+', '"pid":1', text)
    text = re.sub(r'as pid \d+', 'as pid 1', text)
    text = re.sub(r'released · peak [0-9.]+ [MG]B · [0-9.]+ s CPU in [0-9.]+ s', 'released · <cost>', text)
    return text
