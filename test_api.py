import httpx
import json

payload = {
    'message': 'how can i come out of this i want to end this',
    'session_id': 'test-session-123',
    'assessment_results': {},
    'conversation_history': []
}

try:
    with httpx.stream('POST', 'http://localhost:8000/api/chat/stream', json=payload, timeout=30.0) as r:
        for line in r.iter_lines():
            if line:
                print(line)
except Exception as e:
    print('Failed:', e)
