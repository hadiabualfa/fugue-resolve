class FugueBlueprint:
    def __init__(self):
        self.state = 'INITIAL' 
        self.motives = {
            'subject': None, 'answer': None, 'cs1': None, 
            'cs2': None, 'free_melody': None, 'episode_line': None
        }
        self.motive_sources = {
            'subject': 1,
            'answer': 2,
            'cs1': 0,
            'cs2': 0,
            'free_melody': 0,
            'episode_line': 0,
        }
        self.middle_entry_count = 0
        self.episode_count = 0
        self.last_subject_voice = None
        self.last_episode_rest_voice = 1
        
        self.current_harmony = 'I'

    def advance(self, decision='auto'):
        if self.state == 'INITIAL':
            self.state = 'EXPO_2'
            self.current_harmony = 'V' 
            self.last_subject_voice = 2
            return {0: 'cs1', 1: 'rest', 2: 'answer'}
            
        elif self.state == 'EXPO_2':
            self.state = 'EXPO_3'
            self.current_harmony = 'I' 
            self.last_subject_voice = 1
            return {0: 'cs2', 1: 'subject', 2: 'rest'}
            
        elif self.state == 'EXPO_3':
            if decision == 'middle_entry':
                self.state = 'MIDDLE_ENTRY'
                self.current_harmony = 'I' 
                return self._get_middle_entry_instructions()
            else:
                self.state = 'EPISODE'
                self.current_harmony = 'IV' 
                return self._get_episode_instructions(previous_state='EXPO_3')
                
        elif self.state == 'EPISODE':
            if decision == 'middle_entry':
                self.state = 'MIDDLE_ENTRY'
                self.current_harmony = 'I' 
                return self._get_middle_entry_instructions()
            else:
                progression = {'I': 'IV', 'IV': 'V', 'V': 'I'}
                self.current_harmony = progression.get(self.current_harmony, 'IV')
                return self._get_episode_instructions(previous_state='EPISODE')
                
        elif self.state == 'MIDDLE_ENTRY':
            if decision == 'episode':
                prev_state = self.state
                self.state = 'EPISODE'
                self.current_harmony = 'IV' 
                return self._get_episode_instructions(previous_state=prev_state)
            else:
                self.state = 'MIDDLE_ENTRY'
                self.current_harmony = 'I' 
                return self._get_middle_entry_instructions()
                
        return {0: 'free_melody', 1: 'free_melody', 2: 'free_melody'}

    def _get_middle_entry_instructions(self):
        self.middle_entry_count += 1
        support_role = 'episode_line' if self.episode_count > 0 else 'cs2'
        if self.state == 'EXPO_3':
            subject_voice = 2
        elif self.state == 'EPISODE':
            subject_voice = self.last_episode_rest_voice
        else:
            subject_voice = self.last_subject_voice if self.last_subject_voice is not None else 1

        self.last_subject_voice = subject_voice

        if subject_voice == 0:
            return {0: 'subject', 1: 'free_melody', 2: support_role}
        elif subject_voice == 1:
            return {0: 'cs2', 1: 'subject', 2: 'free_melody'}
        else:
            return {0: support_role, 1: 'free_melody', 2: 'subject'}

    def _get_episode_instructions(self, previous_state):
        self.episode_count += 1
        if previous_state in ['EXPO_3', 'MIDDLE_ENTRY']:
            self.last_episode_rest_voice = self.last_subject_voice if self.last_subject_voice is not None else 1

        rest_voice = self.last_episode_rest_voice

        if rest_voice == 0:
            return {0: 'rest', 1: 'free_melody', 2: 'cs1'}
        elif rest_voice == 1:
            return {0: 'cs1', 1: 'rest', 2: 'free_melody'}
        else:
            return {0: 'cs1', 1: 'free_melody', 2: 'rest'}
