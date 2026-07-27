import sys, os
sys.path.insert(0, os.path.abspath('app'))
from compose import build_voice_system

class Style:
    def __init__(self):
        self.formality = 1
        self.warmth = 3
        self.directness = 3
        self.sentence_length = 'flowing'
        self.hedging = 'neutral'
        self.humor = 'none'
        self.person_focus = 'recipient_first'
        self.proof_density = 'single'
        self.notes = ''
        self.examples = []

class Evidence:
    def __init__(self):
        self.identity_note = ''

class Voice:
    def __init__(self):
        self.allow_dashes = False
        self.style = Style()
        self.evidence = Evidence()
        self.id = 'test'

print(build_voice_system(Voice(), True))
