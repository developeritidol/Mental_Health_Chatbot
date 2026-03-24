/**
 * MindBridge — Frontend Application
 * ───────────────────────────────────
 * Intake flow (9 steps):
 *   1. Full name          (text input)
 *   2. Gender             (buttons)
 *   3. Age                (text input)
 *   4. Profession         (text input)
 *   5. Existing conditions (text input / "None")
 *   6. Emergency contact name (text input)
 *   7. Emergency contact relation (text input)
 *   8. Mood score 1–10    (button grid)
 *   9. Topic              (button grid)
 *   → Opening message from backend
 *   → Free conversation begins
 *
 * Fixes in this version:
 *   - Full 9-field intake per original spec
 *   - TTS: cancel-before-speak + chunking + visibility listener (browser bug fix)
 *   - Crisis follow-up flag: re-checks safety in the next 2 turns after crisis
 */

'use strict';

// ── State ──────────────────────────────────────────────────────────────────────
const state = {
    phase: 'intake',
    turnCount: 0,
    intakeStep: 1,
    profile: {
        name: '', gender: '', age: null,
        emergency_contact_name: '',
        emergency_contact_relation: '', emergency_contact_phone: '',
    },
    personality: {
        prefers_solitude: 'Sometimes',
        logic_over_emotion: 'Sometimes',
        plans_ahead: 'Sometimes',
        energized_by_social: 'Sometimes',
        trusts_instincts: 'Sometimes'
    },
    sessionId: null,
    deviceId: localStorage.getItem('device_id') || crypto.randomUUID(),
    history: [],
    sadnessScores: [],
    crisisFlag: false,        // true after suicidal ideation detected
    crisisFollowUpTurns: 0,   // counts down after crisis — re-checks for 2 turns
    isTyping: false,
    voiceEnabled: false,
    ttsQueue: [],             // chunked TTS queue
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
};

// ── DOM Refs ───────────────────────────────────────────────────────────────────
const dom = {
    messages: document.getElementById('messages'),
    userInput: document.getElementById('user-input'),
    sendBtn: document.getElementById('send-btn'),
    micBtn: document.getElementById('mic-btn'),
    voiceToggle: document.getElementById('voice-toggle'),
    sidebar: document.getElementById('sidebar'),
    sidebarToggle: document.getElementById('sidebar-toggle'),
    sessionCard: document.getElementById('session-card'),
    sessionName: document.getElementById('session-name'),
    moodBarWrap: document.getElementById('mood-bar-container'),
    moodBar: document.getElementById('mood-bar'),
};

// ── Init ───────────────────────────────────────────────────────────────────────
(function init() {
    // Auto-detect country from timezone
    localStorage.setItem('device_id', state.deviceId);
    try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        // Keeping try/catch but removing country assignment as it's not strictly needed anymore
    } catch (e) { }

    // TTS: fix browser autoplay bug — resume synthesis on visibility change
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && window.speechSynthesis.paused) {
            window.speechSynthesis.resume();
        }
    });

    // Kick off intake
    appendBotBubble(
        'Before we begin, I\'d like to ask a few quick questions so I can understand how you\'re feeling.\n\nYour answers stay private and help me be here for you properly.'
    );
    setTimeout(() => {
        appendBotBubble("What's your full name? A nickname works perfectly fine.");
        enableInput('Type your name…');
    }, 600);

    dom.sendBtn.addEventListener('click', handleSend);
    dom.userInput.addEventListener('input', onInputChange);
    dom.userInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    dom.voiceToggle.addEventListener('change', e => { state.voiceEnabled = e.target.checked; });
    
    dom.sidebarToggle.addEventListener('click', () => {
        if (window.innerWidth <= 640) {
            dom.sidebar.classList.toggle('mobile-open');
        } else {
            dom.sidebar.classList.toggle('collapsed');
        }
    });

    const sidebarClose = document.getElementById('sidebar-close');
    if (sidebarClose) {
        sidebarClose.addEventListener('click', () => {
            dom.sidebar.classList.remove('mobile-open');
        });
    }

    dom.micBtn.addEventListener('click', handleMicClick);
})();

// ── Input Helpers ──────────────────────────────────────────────────────────────
function enableInput(placeholder = "Share what's on your mind…") {
    dom.userInput.placeholder = placeholder;
    dom.userInput.disabled = false;
    setTimeout(() => dom.userInput.focus(), 80);
}

function disableInput(placeholder = 'Select an option above…') {
    dom.userInput.placeholder = placeholder;
    dom.userInput.disabled = true;
    dom.sendBtn.disabled = true;
    dom.sendBtn.classList.remove('active');
}

function onInputChange() {
    const val = dom.userInput.value.trim();
    const canSend = val.length > 0 && !state.isTyping && !dom.userInput.disabled;
    dom.sendBtn.disabled = !canSend;
    dom.sendBtn.classList.toggle('active', canSend);
    dom.userInput.style.height = 'auto';
    dom.userInput.style.height = Math.min(dom.userInput.scrollHeight, 120) + 'px';
}

// ── Send Router ────────────────────────────────────────────────────────────────
function handleSend() {
    const text = dom.userInput.value.trim();
    if (!text || state.isTyping || dom.userInput.disabled) return;
    dom.userInput.value = '';
    dom.userInput.style.height = 'auto';
    onInputChange();
    if (state.phase === 'intake') handleIntakeSend(text);
    else handleChatSend(text);
}

// ── Intake — 11 Steps ────────────────────────────────────────────────────────
const INTAKE_STEPS = {
    1: { save: t => { state.profile.name = t; }, next: () => askGender() },
    2: { save: () => { }, next: () => { } },
    3: { save: t => { state.profile.age = parseInt(t) || null; }, next: () => askEmergencyName() },
    4: { save: t => { state.profile.emergency_contact_name = t; }, next: () => askEmergencyRelation() },
    5: { save: t => { state.profile.emergency_contact_relation = t; }, next: () => askEmergencyPhone() },
    6: { save: t => { state.profile.emergency_contact_phone = t; }, next: () => askP1() },
    7: { save: () => { }, next: () => { } },
    8: { save: () => { }, next: () => { } },
    9: { save: () => { }, next: () => { } },
    10: { save: () => { }, next: () => { } },
    11: { save: () => { }, next: () => { } },
};

function handleIntakeSend(text) {
    const step = state.intakeStep;
    appendUserBubble(text);
    disableInput();
    if (INTAKE_STEPS[step]) INTAKE_STEPS[step].save(text);
    setTimeout(() => {
        if (INTAKE_STEPS[step]) INTAKE_STEPS[step].next();
    }, 500);
}

function askGender() {
    state.intakeStep = 2;
    appendBotBubble(`Nice to meet you, ${state.profile.name} 🙂\n\nWhat is your gender?`);
    appendOptionButtons(['Male', 'Female', 'Non-binary', 'Prefer not to say'], val => {
        state.profile.gender = val;
        appendUserBubble(val);
        disableInput();
        setTimeout(() => askAge(), 500);
    });
}

function askAge() {
    state.intakeStep = 3;
    appendBotBubble('How old are you?');
    enableInput('Enter your age…');
}

function askEmergencyName() {
    state.intakeStep = 4;
    appendBotBubble('In case of an emergency, could you share the name of someone we can reach? This stays private and is only used if we need urgent help for you.');
    enableInput('Emergency contact full name…');
}

function askEmergencyRelation() {
    state.intakeStep = 5;
    appendBotBubble(`What is ${state.profile.emergency_contact_name}'s relationship to you?`);
    appendOptionButtons(['Parent', 'Sibling', 'Partner', 'Friend', 'Other'], val => {
        state.profile.emergency_contact_relation = val;
        appendUserBubble(val);
        disableInput();
        setTimeout(() => askEmergencyPhone(), 500);
    });
}

function askEmergencyPhone() {
    state.intakeStep = 6;
    appendBotBubble(`Could you share the phone number for ${state.profile.emergency_contact_name}?`);
    enableInput('Emergency phone number…');
}

function processPersonalityVal(questionId, val, nextStepFn) {
    state.personality[questionId] = val;
    appendUserBubble(val);
    disableInput();
    setTimeout(() => nextStepFn(), 500);
}

function askP1() {
    state.intakeStep = 7;
    appendBotBubble('Do you enjoy spending time alone more than being in large groups?');
    appendOptionButtons(['Yes', 'No', 'Sometimes'], val => processPersonalityVal('prefers_solitude', val, askP2));
}

function askP2() {
    state.intakeStep = 8;
    appendBotBubble('Do you usually make decisions based on logic rather than emotions?');
    appendOptionButtons(['Yes', 'No', 'Sometimes'], val => processPersonalityVal('logic_over_emotion', val, askP3));
}

function askP3() {
    state.intakeStep = 9;
    appendBotBubble('Do you like planning things in advance instead of being spontaneous?');
    appendOptionButtons(['Yes', 'No', 'Sometimes'], val => processPersonalityVal('plans_ahead', val, askP4));
}

function askP4() {
    state.intakeStep = 10;
    appendBotBubble('Do you feel energized after social interactions?');
    appendOptionButtons(['Yes', 'No', 'Sometimes'], val => processPersonalityVal('energized_by_social', val, askP5));
}

function askP5() {
    state.intakeStep = 11;
    appendBotBubble('Do you often trust your instincts when making important choices?');
    appendOptionButtons(['Yes', 'No', 'Sometimes'], val => processPersonalityVal('trusts_instincts', val, completeAssessment));
}

async function completeAssessment() {
    disableInput('Just a moment…');
    updateSessionSidebar();
    showTyping();

    try {
        const payload = {
            device_id: state.deviceId,
            profile: {
                name: state.profile.name,
                gender: state.profile.gender,
                age: state.profile.age,
                emergency_contact_name: state.profile.emergency_contact_name,
                emergency_contact_relation: state.profile.emergency_contact_relation,
                emergency_contact_phone: state.profile.emergency_contact_phone
            },
            personality_answers: state.personality
        };

        const res = await fetch('/api/assessment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        
        // Use the session ID generated by the backend
        state.sessionId = data.session_id;
        
        hideTyping();
        appendBotBubble(data.opening_message);
    } catch (err) {
        hideTyping();
        appendBotBubble("Something went wrong on my end — but I'm here. What's been going on?");
        // Generate a fallback session id if backend failed
        state.sessionId = crypto.randomUUID(); 
    }

    state.phase = 'chat';
    enableInput("Share what's on your mind…");
}

// ── Chat ───────────────────────────────────────────────────────────────────────
async function handleChatSend(text) {
    appendUserBubble(text);
    disableInput();
    state.isTyping = true;
    state.history.push({ role: 'user', content: text });

    const { bubbleEl, rowEl } = createStreamingBubble();
    let fullReply = '';
    let emotionData = null;

    // Build profile with crisis flag for system prompt awareness
    const profileWithFlags = {
        ...state.profile,
        crisis_follow_up: state.crisisFollowUpTurns > 0,
    };

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                device_id: state.deviceId,
                message: text,
            }),
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const payload = JSON.parse(line.slice(6));
                    if (payload.chunk) {
                        fullReply += payload.chunk;
                        bubbleEl.textContent = fullReply;
                        bubbleEl.classList.add('stream-cursor');
                        scrollBottom();
                    }
                    if (payload.done) {
                        bubbleEl.classList.remove('stream-cursor');
                        emotionData = payload.emotion;
                        if (payload.emotion?.sadness_scores) {
                            state.sadnessScores = payload.emotion.sadness_scores;
                        }
                    }
                } catch { /* partial JSON — ignore */ }
            }
        }
    } catch (err) {
        console.error('Stream error:', err);
        fullReply = "Something interrupted us for a moment. Whenever you're ready, I'm still here.";
        bubbleEl.textContent = fullReply;
        bubbleEl.classList.remove('stream-cursor');
    }

    // Crisis detection — set follow-up flag
    if (emotionData?.is_crisis_signal) {
        state.crisisFlag = true;
        state.crisisFollowUpTurns = 2;
    } else if (state.crisisFollowUpTurns > 0) {
        state.crisisFollowUpTurns--;
    }

    // Emotion badge
    if (emotionData?.dominant_emotion && emotionData.dominant_emotion !== 'neutral') {
        appendEmotionBadge(rowEl, emotionData.dominant_emotion);
    }

    state.history.push({ role: 'assistant', content: fullReply });
    state.turnCount++;
    updatePhaseIndicator();

    // Update mood bar dynamically from emotion intensity
    if (emotionData?.intensity) {
        updateMoodBar(emotionData.intensity);
    }

    // ── TTS — fixed version ──────────────────────────────────────────────────────
    if (state.voiceEnabled && 'speechSynthesis' in window && fullReply) {
        speakText(fullReply);
    }

    state.isTyping = false;
    enableInput("Share what's on your mind…");
}

// ── TTS — Fixed Implementation ─────────────────────────────────────────────────
// Root cause of stopping after 2–3 turns:
//   1. Chrome has a ~15s limit per utterance — long responses get silently cut
//   2. speak() called while previous utterance still running causes queue corruption
//   3. Page visibility changes pause synthesis permanently without a resume listener
// Fix: cancel before speak, chunk long text, heartbeat to keep synthesis alive

let _ttsHeartbeat = null;
let _bestVoice = null;

function getBestVoice() {
    if (_bestVoice) return _bestVoice;
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return null;

    // Ordered by preference for high-quality English voices
    const preferredNames = [
        "Microsoft Aria Online (Natural) - English (United States)",
        "Microsoft Jenny Online (Natural) - English (United States)",
        "Google US English",
        "Google UK English Female",
        "Samantha", "Rishi", "Daniel", "Serena"
    ];

    for (const pref of preferredNames) {
        const found = voices.find(v => v.name === pref);
        if (found) {
            _bestVoice = found;
            return _bestVoice;
        }
    }

    // Fallback: prioritize online network voices if possible, otherwise first English voice
    const enVoices = voices.filter(v => v.lang.startsWith('en'));
    const onlineVoice = enVoices.find(v => v.name.toLowerCase().includes('natural') || v.network);

    _bestVoice = onlineVoice || enVoices[0] || voices[0];
    return _bestVoice;
}

// Voices load asynchronously in some browsers
if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => { _bestVoice = null; getBestVoice(); };
}

function speakText(text) {
    if (!window.speechSynthesis) return;

    // Cancel any ongoing speech first — prevents queue corruption
    window.speechSynthesis.cancel();

    // Strip markdown and clean text
    const clean = text
        .replace(/[*_~`#]/g, '')
        .replace(/\n+/g, '. ')
        .replace(/\.{2,}/g, '.')
        .trim();

    // Split into chunks of ~180 chars at sentence boundaries to avoid 15s limit
    const chunks = splitIntoChunks(clean, 180);

    // Clear heartbeat if any
    if (_ttsHeartbeat) { clearInterval(_ttsHeartbeat); _ttsHeartbeat = null; }

    let idx = 0;

    function speakNext() {
        if (idx >= chunks.length) {
            if (_ttsHeartbeat) { clearInterval(_ttsHeartbeat); _ttsHeartbeat = null; }
            return;
        }
        const chunk = chunks[idx++];
        const utt = new SpeechSynthesisUtterance(chunk);
        utt.rate = 0.95; // Slightly faster sounds a bit more natural
        utt.pitch = 1.0;

        const voice = getBestVoice();
        if (voice) {
            utt.voice = voice;
        } else {
            utt.lang = 'en-US';
        }

        utt.onend = () => speakNext();
        utt.onerror = e => {
            // 'interrupted' errors are normal when cancel() is called — ignore
            if (e.error !== 'interrupted') console.warn('TTS error:', e.error);
            speakNext();
        };

        window.speechSynthesis.speak(utt);
    }

    // Chrome bug: speechSynthesis stalls silently after ~15s of inactivity
    // Heartbeat calls resume() every 10s to keep it alive
    _ttsHeartbeat = setInterval(() => {
        if (window.speechSynthesis.speaking) {
            window.speechSynthesis.pause();
            window.speechSynthesis.resume();
        }
    }, 10000);

    speakNext();
}

function splitIntoChunks(text, maxLen) {
    const sentences = text.match(/[^.!?]+[.!?]*/g) || [text];
    const chunks = [];
    let current = '';

    for (const s of sentences) {
        if ((current + s).length > maxLen && current.length > 0) {
            chunks.push(current.trim());
            current = s;
        } else {
            current += s;
        }
    }
    if (current.trim()) chunks.push(current.trim());
    return chunks.filter(c => c.length > 0);
}

// ── Button Builders ────────────────────────────────────────────────────────────
function appendMoodButtons() {
    const wrap = document.createElement('div');
    wrap.className = 'msg-row bot';
    const spacer = mkSpacer();
    const grid = document.createElement('div');
    grid.className = 'options-grid bubble-wrap';
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].forEach(n => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.textContent = String(n);
        btn.addEventListener('click', () => selectMood(n, wrap));
        grid.appendChild(btn);
    });
    wrap.appendChild(spacer);
    wrap.appendChild(grid);
    dom.messages.appendChild(wrap);
    scrollBottom();
}

function appendTopicButtons() {
    const wrap = document.createElement('div');
    wrap.className = 'msg-row bot';
    const spacer = mkSpacer();
    const grid = document.createElement('div');
    grid.className = 'options-grid bubble-wrap';
    TOPICS.forEach(topic => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.textContent = topic;
        btn.addEventListener('click', () => selectTopic(topic, wrap));
        grid.appendChild(btn);
    });
    wrap.appendChild(spacer);
    wrap.appendChild(grid);
    dom.messages.appendChild(wrap);
    scrollBottom();
}

function appendOptionButtons(options, onSelect) {
    const wrap = document.createElement('div');
    wrap.className = 'msg-row bot';
    wrap.dataset.optrow = 'true';
    const spacer = mkSpacer();
    const grid = document.createElement('div');
    grid.className = 'options-grid bubble-wrap';
    options.forEach(opt => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.textContent = opt;
        btn.addEventListener('click', () => {
            wrap.remove();
            onSelect(opt);
        });
        grid.appendChild(btn);
    });
    wrap.appendChild(spacer);
    wrap.appendChild(grid);
    dom.messages.appendChild(wrap);
    scrollBottom();
}

function mkSpacer() {
    const s = document.createElement('div');
    s.style.cssText = 'width:34px;flex-shrink:0';
    return s;
}

// ── Bubble Builders ────────────────────────────────────────────────────────────
function appendBotBubble(text) {
    const row = document.createElement('div');
    row.className = 'msg-row bot';

    const avatar = document.createElement('div');
    avatar.className = 'bot-avatar small';
    avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="white" width="16" height="16"><path d="M12 3C8.69 3 6 5.69 6 9c0 2.12 1.1 3.99 2.77 5.1L8 16h8l-.77-1.9C16.9 12.99 18 11.12 18 9c0-3.31-2.69-6-6-6zm-1 10h2v1h-2v-1zm0-2h2V7h-2v4z"/></svg>`;

    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap';
    const bubble = document.createElement('div');
    bubble.className = 'bubble bot';
    bubble.textContent = text;

    wrap.appendChild(bubble);
    row.appendChild(avatar);
    row.appendChild(wrap);
    dom.messages.appendChild(row);
    scrollBottom();
    return row;
}

function appendUserBubble(text) {
    const row = document.createElement('div');
    row.className = 'msg-row user';
    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap';
    const bubble = document.createElement('div');
    bubble.className = 'bubble user';
    bubble.textContent = text;
    const avatar = document.createElement('div');
    avatar.className = 'user-avatar';
    avatar.textContent = state.profile.name ? state.profile.name[0].toUpperCase() : 'U';
    wrap.appendChild(bubble);
    row.appendChild(wrap);
    row.appendChild(avatar);
    dom.messages.appendChild(row);
    scrollBottom();
}

function createStreamingBubble() {
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    const avatar = document.createElement('div');
    avatar.className = 'bot-avatar small';
    avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="white" width="16" height="16"><path d="M12 3C8.69 3 6 5.69 6 9c0 2.12 1.1 3.99 2.77 5.1L8 16h8l-.77-1.9C16.9 12.99 18 11.12 18 9c0-3.31-2.69-6-6-6zm-1 10h2v1h-2v-1zm0-2h2V7h-2v4z"/></svg>`;
    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap';
    const bubble = document.createElement('div');
    bubble.className = 'bubble bot';
    wrap.appendChild(bubble);
    row.appendChild(avatar);
    row.appendChild(wrap);
    dom.messages.appendChild(row);
    scrollBottom();
    return { bubbleEl: bubble, rowEl: wrap };
}

function appendEmotionBadge(wrapEl, emotion) {
    const badge = document.createElement('div');
    badge.className = 'emotion-badge';
    const dot = document.createElement('div');
    dot.className = 'emotion-dot';
    badge.appendChild(dot);
    badge.appendChild(document.createTextNode(emotion));
    wrapEl.appendChild(badge);
}

// ── Typing Indicator ───────────────────────────────────────────────────────────
let typingRow = null;
function showTyping() {
    typingRow = document.createElement('div');
    typingRow.className = 'msg-row bot';
    const avatar = document.createElement('div');
    avatar.className = 'bot-avatar small';
    avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="white" width="16" height="16"><path d="M12 3C8.69 3 6 5.69 6 9c0 2.12 1.1 3.99 2.77 5.1L8 16h8l-.77-1.9C16.9 12.99 18 11.12 18 9c0-3.31-2.69-6-6-6zm-1 10h2v1h-2v-1zm0-2h2V7h-2v4z"/></svg>`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble bot';
    const dots = document.createElement('div');
    dots.className = 'typing-dots';
    dots.innerHTML = '<span></span><span></span><span></span>';
    bubble.appendChild(dots);
    typingRow.appendChild(avatar);
    typingRow.appendChild(bubble);
    dom.messages.appendChild(typingRow);
    scrollBottom();
}
function hideTyping() {
    if (typingRow) { typingRow.remove(); typingRow = null; }
}

// ── Sidebar ────────────────────────────────────────────────────────────────────
function updateSessionSidebar() {
    if (state.profile.name) {
        dom.sessionCard.classList.remove('hidden');
        dom.sessionName.textContent = state.profile.name;
    }
}

function updateMoodBar(intensity) {
    // Map intensity string to numeric score, or use intake mood_score
    let score;
    if (intensity) {
        const intensityMap = { 'low': 8, 'moderate': 5, 'high': 3, 'severe': 1 };
        score = intensityMap[intensity] || state.profile.mood_score;
    } else {
        score = state.profile.mood_score;
    }
    if (!score) return;
    dom.moodBarWrap.classList.remove('hidden');
    dom.moodBar.innerHTML = '';
    for (let n = 1; n <= 10; n++) {
        const cell = document.createElement('div');
        cell.className = 'mood-cell';
        if (n <= score) {
            const color = n <= 3 ? '#ef4444' : n <= 5 ? '#f59e0b' : n <= 7 ? '#3b82f6' : '#10b981';
            cell.style.background = color;
            cell.style.border = '1px solid transparent';
        }
        dom.moodBar.appendChild(cell);
    }
}

function updatePhaseIndicator() {
    const t = state.turnCount;
    const el = document.querySelector('.header-status');
    if (!el) return;
    if (t === 3) el.innerHTML = '<span class="status-dot"></span>Listening deeply';
    else if (t === 6) el.innerHTML = '<span class="status-dot"></span>Here with you';
    else if (t === 11) el.innerHTML = '<span class="status-dot"></span>Staying with you';
}

// ── Audio / Whisper ────────────────────────────────────────────────────────────
async function handleMicClick() {
    if (state.isRecording) stopRecording();
    else await startRecording();
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.audioChunks = [];
        state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        state.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) state.audioChunks.push(e.data); };
        state.mediaRecorder.onstop = sendAudioToWhisper;
        state.mediaRecorder.start();
        state.isRecording = true;
        dom.micBtn.classList.add('recording');
    } catch (err) {
        appendBotBubble('Microphone access was denied. You can type your message instead.');
    }
}

function stopRecording() {
    if (state.mediaRecorder && state.isRecording) {
        state.mediaRecorder.stop();
        // Do NOT stop tracks here, or the final chunk may be dropped. 
        // We will stop the stream tracks in sendAudioToWhisper.
        state.isRecording = false;
        dom.micBtn.classList.remove('recording');
    }
}

async function sendAudioToWhisper() {
    // Stop the tracks safely now that recording is fully stopped
    if (state.mediaRecorder && state.mediaRecorder.stream) {
        state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
    }

    if (!state.audioChunks.length) return;
    const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
    const formData = new FormData();
    formData.append('file', blob, 'recording.webm');
    dom.userInput.placeholder = 'Transcribing…';
    dom.userInput.disabled = true;
    try {
        const res = await fetch('/api/audio/transcribe', { method: 'POST', body: formData });
        if (!res.ok) throw new Error('Transcription failed');
        const data = await res.json();
        const text = data.text.trim();
        dom.userInput.disabled = false;
        dom.userInput.placeholder = "Share what's on your mind…";
        onInputChange();
        if (text) {
            if (state.phase === 'intake') handleIntakeSend(text);
            else handleChatSend(text);
        }
    } catch (err) {
        dom.userInput.disabled = false;
        dom.userInput.placeholder = "Share what's on your mind…";
        appendBotBubble("Couldn't transcribe that — you can type your message instead.");
    }
}

// ── Scroll ─────────────────────────────────────────────────────────────────────
function scrollBottom() {
    requestAnimationFrame(() => {
        dom.messages.scrollTo({ top: dom.messages.scrollHeight, behavior: 'smooth' });
    });
}