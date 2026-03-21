/**
 * MindBridge — Frontend Application
 * ───────────────────────────────────
 * Talks to the FastAPI backend via:
 *   POST /api/chat/opening   — get personalised first message
 *   POST /api/chat/stream    — SSE streaming chat (real-time tokens)
 *   POST /api/audio/transcribe — Groq Whisper STT
 *
 * Intake flow:
 *   Step 1 → name (text input)
 *   Step 2 → mood score 1-10 (button grid)
 *   Step 3 → topic (button grid)
 *   → Opening message from backend
 *   → Free conversation begins
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
    phase: 'intake',
    turnCount: 0,
    intakeStep: 1,         // 1 = name, 2 = mood, 3 = topic
    profile: { name: '', mood_score: null, topic: '', country: 'IN' },
    sessionId: null,
    history: [],           // [{role, content}] — sent to backend each turn
    sadnessScores: [],     // float[] — for trend monitoring
    isTyping: false,
    voiceEnabled: false,
    // Audio recording
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
};

// ── DOM Refs ──────────────────────────────────────────────────────────────────
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

// ── Init ──────────────────────────────────────────────────────────────────────
(function init() {
    // Auto-detect country for crisis line selection
    try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
        if (tz.includes("Asia/Kolkata") || tz.includes("Asia/Calcutta")) state.profile.country = "IN";
        else if (tz.startsWith("America/")) state.profile.country = "US";
        else if (tz.startsWith("Europe/London")) state.profile.country = "UK";
        else if (tz.startsWith("Australia/")) state.profile.country = "AU";
        else if (tz.startsWith("America/Toronto") || tz.startsWith("America/Vancouver")) state.profile.country = "CA";
    } catch (e) { }

    // Intake: first bot message
    appendBotBubble(
        `Before we begin, I'd like to ask a few quick questions so I can understand how you're feeling.\n\nYour answers stay private and help me be here for you properly.`,
        null,
        'step-0'
    );
    setTimeout(() => {
        appendBotBubble("What's your name? A nickname works perfectly fine.", null, 'step-1');
        enableInput("Type your name…");
    }, 600);

    // Event listeners
    dom.sendBtn.addEventListener('click', handleSend);
    dom.userInput.addEventListener('input', onInputChange);
    dom.userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    dom.voiceToggle.addEventListener('change', (e) => { state.voiceEnabled = e.target.checked; });
    dom.sidebarToggle.addEventListener('click', () => {
        dom.sidebar.classList.toggle('collapsed');
    });
    dom.micBtn.addEventListener('click', handleMicClick);
})();

// ── Input helpers ─────────────────────────────────────────────────────────────
function enableInput(placeholder = "Share what's on your mind…") {
    dom.userInput.placeholder = placeholder;
    dom.userInput.disabled = false;
    dom.userInput.focus();
}

function disableInput(placeholder = "Select an option above…") {
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
    // Auto-resize textarea
    dom.userInput.style.height = 'auto';
    dom.userInput.style.height = Math.min(dom.userInput.scrollHeight, 120) + 'px';
}

// ── Main send handler ─────────────────────────────────────────────────────────
function handleSend() {
    const text = dom.userInput.value.trim();
    if (!text || state.isTyping || dom.userInput.disabled) return;
    dom.userInput.value = '';
    dom.userInput.style.height = 'auto';
    onInputChange();

    if (state.phase === 'intake') handleIntakeSend(text);
    else handleChatSend(text);
}

// ── Intake ────────────────────────────────────────────────────────────────────
function handleIntakeSend(text) {
    if (state.intakeStep === 1) {
        state.profile.name = text;
        appendUserBubble(text);
        disableInput();
        state.intakeStep = 2;
        setTimeout(() => {
            appendBotBubble(
                `Nice to meet you, ${text} 🙂\n\nOn a scale from 1–10, how are you feeling right now?\n1 = really struggling  ·  10 = doing great`,
                null,
                'step-2'
            );
            appendMoodButtons();
        }, 650);
    }
}

function appendMoodButtons() {
    const wrap = document.createElement('div');
    wrap.className = 'msg-row bot';
    const avatarSpacer = document.createElement('div');
    avatarSpacer.style.width = '34px';
    avatarSpacer.style.flexShrink = '0';
    const grid = document.createElement('div');
    grid.className = 'options-grid bubble-wrap';
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].forEach(n => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.textContent = String(n);
        btn.addEventListener('click', () => selectMood(n, wrap));
        grid.appendChild(btn);
    });
    wrap.appendChild(avatarSpacer);
    wrap.appendChild(grid);
    dom.messages.appendChild(wrap);
    scrollBottom();
}

function selectMood(score, buttonRow) {
    state.profile.mood_score = score;
    buttonRow.remove();
    appendUserBubble(`${score} / 10`);
    state.intakeStep = 3;
    setTimeout(() => {
        appendBotBubble("What's been on your mind lately? No pressure to explain everything — pick what feels closest.", null, 'step-3');
        appendTopicButtons();
        updateSessionSidebar();
    }, 650);
}

const TOPICS = [
    "Stress & anxiety", "Feeling lonely", "Relationship issues",
    "Work or studies", "Grief or loss", "Just need to talk",
];

function appendTopicButtons() {
    const wrap = document.createElement('div');
    wrap.className = 'msg-row bot';
    const spacer = document.createElement('div');
    spacer.style.width = '34px';
    spacer.style.flexShrink = '0';
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

async function selectTopic(topic, buttonRow) {
    state.profile.topic = topic;
    buttonRow.remove();
    appendUserBubble(topic);
    disableInput("Just a moment…");
    updateSessionSidebar();

    showTyping();
    try {
        const res = await fetch('/api/chat/opening', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(state.profile),
        });
        const data = await res.json();
        state.sessionId = data.session_id;
        hideTyping();
        appendBotBubble(data.message);
    } catch (err) {
        hideTyping();
        appendBotBubble("Something went wrong on my end — but I'm here. What's been going on?");
    }

    state.phase = 'chat';
    enableInput("Share what's on your mind…");
    updateMoodBar();
}

// ── Chat ──────────────────────────────────────────────────────────────────────
async function handleChatSend(text) {
    appendUserBubble(text);
    disableInput();
    state.isTyping = true;

    // Optimistically add user to local history
    state.history.push({ role: 'user', content: text });

    // Create streaming bubble
    const { bubbleEl, rowEl } = createStreamingBubble();
    let fullReply = '';
    let emotionData = null;

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                message: text,
                profile: state.profile,
                history: state.history.slice(-40),
                sadness_scores: state.sadnessScores,   // send current scores to backend
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
            buffer = lines.pop();                   // keep incomplete last line

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
                        // Server returns updated sadness scores — keep them for next request
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

    // Append emotion badge if available
    if (emotionData && emotionData.dominant_emotion && emotionData.dominant_emotion !== 'neutral') {
        appendEmotionBadge(rowEl, emotionData.dominant_emotion);
    }

    // Save assistant reply to history
    state.history.push({ role: 'assistant', content: fullReply });
    state.turnCount++;
    updatePhaseIndicator();

    // TTS if enabled
    if (state.voiceEnabled && 'speechSynthesis' in window && fullReply) {
        const utterance = new SpeechSynthesisUtterance(fullReply.replace(/[*_~`#]/g, ''));
        utterance.rate = 0.9;
        utterance.pitch = 1.0;
        speechSynthesis.speak(utterance);
    }

    state.isTyping = false;
    enableInput("Share what's on your mind…");
}

// ── Bubble builders ───────────────────────────────────────────────────────────
function appendBotBubble(text, emotionLabel, dataKey) {
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    if (dataKey) row.dataset.step = dataKey;

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
    bubble.textContent = '';

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

// ── Typing indicator ──────────────────────────────────────────────────────────
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

// ── Sidebar updates ───────────────────────────────────────────────────────────
function updateSessionSidebar() {
    if (state.profile.name) {
        dom.sessionCard.classList.remove('hidden');
        dom.sessionName.textContent = state.profile.name;
    }
}

function updateMoodBar() {
    const score = state.profile.mood_score;
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

// ── Audio / Whisper ───────────────────────────────────────────────────────────
async function handleMicClick() {
    if (state.isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.audioChunks = [];
        state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        state.mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) state.audioChunks.push(e.data);
        };
        state.mediaRecorder.onstop = sendAudioToWhisper;
        state.mediaRecorder.start();
        state.isRecording = true;
        dom.micBtn.classList.add('recording');
        dom.micBtn.title = 'Click to stop recording';
    } catch (err) {
        console.error('Mic error:', err);
        appendBotBubble('Microphone access was denied. You can type your message instead.');
    }
}

function stopRecording() {
    if (state.mediaRecorder && state.isRecording) {
        state.mediaRecorder.stop();
        state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
        state.isRecording = false;
        dom.micBtn.classList.remove('recording');
        dom.micBtn.title = 'Hold to record';
    }
}

async function sendAudioToWhisper() {
    if (state.audioChunks.length === 0) return;
    const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
    const formData = new FormData();
    formData.append('file', blob, 'recording.webm');

    // Show transcribing state
    dom.userInput.placeholder = 'Transcribing…';
    dom.userInput.disabled = true;

    try {
        const res = await fetch('/api/audio/transcribe', { method: 'POST', body: formData });
        if (!res.ok) throw new Error('Transcription failed');
        const data = await res.json();
        dom.userInput.value = data.text;
        dom.userInput.disabled = false;
        dom.userInput.placeholder = "Share what's on your mind…";
        onInputChange();
        dom.userInput.focus();
    } catch (err) {
        console.error('Whisper error:', err);
        dom.userInput.disabled = false;
        dom.userInput.placeholder = "Share what's on your mind…";
        appendBotBubble("Couldn't transcribe that — you can type your message instead.");
    }
}

// ── Scroll ────────────────────────────────────────────────────────────────────
function scrollBottom() {
    requestAnimationFrame(() => {
        dom.messages.scrollTo({ top: dom.messages.scrollHeight, behavior: 'smooth' });
    });
}

// ── Phase indicator (subtle visual feedback in header) ────────────────────────
function updatePhaseIndicator() {
    const phases = { 0: null, 3: 'exploring', 6: 'here for you', 11: 'with you' };
    // Just update document title subtly
    const t = state.turnCount;
    if (t === 3) {
        const el = document.querySelector('.header-status');
        if (el) el.innerHTML = '<span class="status-dot"></span>Listening deeply';
    } else if (t === 6) {
        const el = document.querySelector('.header-status');
        if (el) el.innerHTML = '<span class="status-dot"></span>Here with you';
    }
}