/**
 * MindBridge — Frontend Application Logic
 * Handles: Assessment Phase → Free Chat Phase (SSE Streaming)
 */

// ============================================================
// State Management
// ============================================================
const state = {
    phase: 'assessment',              // 'assessment' | 'chatting'
    currentQuestionIndex: 0,
    assessmentQuestions: [],
    assessmentResults: {},
    chatHistory: [],                   // Sliding window: last 10 messages
    isStreaming: false,
    sessionId: crypto.randomUUID(),
};

const MEMORY_WINDOW = 10; // Keep last 10 messages

// ============================================================
// DOM References
// ============================================================
const messagesFeed = document.getElementById('messagesFeed');
const userInput    = document.getElementById('userInput');
const sendBtn      = document.getElementById('sendBtn');
const micBtn       = document.getElementById('micBtn');
const ttsToggle    = document.getElementById('ttsToggle');
const emotionIndicator = document.getElementById('emotionIndicator');
const emotionLabel = document.getElementById('emotionLabel');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebar = document.getElementById('sidebar');

// ============================================================
// Utility: Add Message Bubble
// ============================================================
function addMessage(role, content, extra = {}) {
    const msg = document.createElement('div');
    msg.classList.add('message', role);

    const avatar = document.createElement('div');
    avatar.classList.add('message-avatar');
    avatar.textContent = role === 'user' ? '🙂' : '🧠';

    const bubble = document.createElement('div');
    bubble.classList.add('message-content');
    bubble.innerHTML = content;

    msg.appendChild(avatar);
    msg.appendChild(bubble);
    messagesFeed.appendChild(msg);
    messagesFeed.scrollTop = messagesFeed.scrollHeight;

    return bubble; // Return for streaming updates
}

// ============================================================
// Utility: Add Assessment Buttons
// ============================================================
function addAssessmentButtons(options, onSelect) {
    const container = document.createElement('div');
    container.classList.add('message', 'assistant');

    const avatar = document.createElement('div');
    avatar.classList.add('message-avatar');
    avatar.textContent = '🧠';

    const bubble = document.createElement('div');
    bubble.classList.add('message-content');

    const btnGroup = document.createElement('div');
    btnGroup.classList.add('assessment-options');

    options.forEach(option => {
        const btn = document.createElement('button');
        btn.classList.add('assessment-btn');
        btn.textContent = option;
        btn.addEventListener('click', () => {
            // Visual feedback
            btnGroup.querySelectorAll('.assessment-btn').forEach(b => b.disabled = true);
            btn.classList.add('selected');
            onSelect(option);
        });
        btnGroup.appendChild(btn);
    });

    bubble.appendChild(btnGroup);
    container.appendChild(avatar);
    container.appendChild(bubble);
    messagesFeed.appendChild(container);
    messagesFeed.scrollTop = messagesFeed.scrollHeight;
}

// ============================================================
// Utility: Typing Indicator
// ============================================================
function showTypingIndicator() {
    const msg = document.createElement('div');
    msg.classList.add('message', 'assistant');
    msg.id = 'typingIndicator';

    const avatar = document.createElement('div');
    avatar.classList.add('message-avatar');
    avatar.textContent = '🧠';

    const bubble = document.createElement('div');
    bubble.classList.add('message-content');

    const dots = document.createElement('div');
    dots.classList.add('typing-indicator');
    dots.innerHTML = '<span></span><span></span><span></span>';

    bubble.appendChild(dots);
    msg.appendChild(avatar);
    msg.appendChild(bubble);
    messagesFeed.appendChild(msg);
    messagesFeed.scrollTop = messagesFeed.scrollHeight;
}

function removeTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

// ============================================================
// Phase 1: Assessment Flow
// ============================================================
async function startAssessment() {
    // Show welcome message
    addMessage('assistant',
        `Hi there 💛 I'm <strong>MindBridge</strong>, your mental health companion.<br><br>` +
        `Before we begin, I'd like to ask a few quick questions so I can better understand how you're feeling. ` +
        `Your answers are private and help me support you better.`
    );

    // Fetch questions from backend
    try {
        const res = await fetch('/api/assessment/questions');
        const data = await res.json();
        state.assessmentQuestions = data.questions;
    } catch (err) {
        // Fallback: hardcoded questions in case backend isn't ready
        state.assessmentQuestions = [
            { id: 'q1_safety', text: 'Are you currently having thoughts of hurting yourself or ending your life?', type: 'choice', options: ['No', 'Sometimes', 'Yes'], is_critical: true },
            { id: 'q2_depressed', text: 'Over the last 2 weeks, how often have you felt down, depressed, or hopeless?', type: 'choice', options: ['Not at all', 'Several days', 'More than half the days', 'Nearly every day'] },
            { id: 'q3_anxious', text: 'How often have you been feeling nervous, anxious, or on edge?', type: 'choice', options: ['Not at all', 'Several days', 'More than half the days', 'Nearly every day'] },
            { id: 'q4_source', text: "What has been the primary source of your stress or concern recently?", type: 'text' },
        ];
    }

    // Start asking first question
    setTimeout(() => askNextQuestion(), 800);
}

function askNextQuestion() {
    const idx = state.currentQuestionIndex;
    if (idx >= state.assessmentQuestions.length) {
        // Assessment complete → transition to chat
        finishAssessment();
        return;
    }

    const q = state.assessmentQuestions[idx];
    addMessage('assistant', q.text);

    if (q.type === 'choice' && q.options) {
        setTimeout(() => {
            addAssessmentButtons(q.options, (selected) => {
                handleAssessmentAnswer(q.id, selected);
            });
        }, 300);
    } else if (q.type === 'text') {
        // Enable free-text input for this question
        userInput.disabled = false;
        sendBtn.disabled = false;
        micBtn.disabled = false;
        userInput.placeholder = 'Type your answer...';
        userInput.focus();

        // Temporarily override send to capture assessment answer
        state._assessmentTextHandler = (text) => {
            handleAssessmentAnswer(q.id, text);
            state._assessmentTextHandler = null;
            userInput.disabled = true;
            sendBtn.disabled = true;
            micBtn.disabled = true;
        };
    }
}

function handleAssessmentAnswer(questionId, answer) {
    // Show user's answer as a bubble
    addMessage('user', answer);

    // Store result
    state.assessmentResults[questionId] = answer;
    state.currentQuestionIndex++;

    // Move to next question after a brief pause
    setTimeout(() => askNextQuestion(), 600);
}

async function finishAssessment() {
    addMessage('assistant',
        `Thank you for sharing that with me 💛 I now have a better understanding of how you're feeling.<br><br>` +
        `From here, you can talk to me about anything on your mind. I'm here to listen.`
    );

    // Switch to chat phase
    state.phase = 'chatting';
    userInput.disabled = false;
    sendBtn.disabled = false;
    micBtn.disabled = false;
    userInput.placeholder = 'Share what\'s on your mind...';
    userInput.focus();

    // Send the assessment to backend to get a personalized opening
    setTimeout(() => {
        sendStreamingMessage('');
    }, 1200);
}

// ============================================================
// Phase 2: Free Chat (SSE Streaming)
// ============================================================
async function sendStreamingMessage(userMessage) {
    if (state.isStreaming) return;
    state.isStreaming = true;

    // Show user message (if not empty — empty means initial greeting after assessment)
    if (userMessage.trim()) {
        addMessage('user', userMessage);
        state.chatHistory.push({ role: 'user', content: userMessage });
    }

    // Trim sliding window
    if (state.chatHistory.length > MEMORY_WINDOW) {
        state.chatHistory = state.chatHistory.slice(-MEMORY_WINDOW);
    }

    // Show typing indicator
    showTypingIndicator();

    // Disable input while streaming
    userInput.disabled = true;
    sendBtn.disabled = true;
    micBtn.disabled = true;

    try {
        const payload = {
            message: userMessage || 'The user just completed the assessment. Please provide a warm, personalized opening message based on their assessment results.',
            session_id: state.sessionId,
            assessment_results: state.assessmentResults,
            conversation_history: state.chatHistory,
        };

        const response = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let aiResponseText = '';
        let responseBubble = null;
        let metadata = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value, { stream: true });
            const lines = text.split('\n');

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;

                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.type === 'metadata') {
                        metadata = data;
                        // Update emotion indicator in header
                        if (data.emotion && data.emotion.label !== 'neutral') {
                            emotionIndicator.style.display = 'flex';
                            emotionLabel.textContent = `${getEmotionEmoji(data.emotion.label)} ${data.emotion.label} (${(data.emotion.score * 100).toFixed(0)}%)`;
                        }
                    } else if (data.type === 'chunk') {
                        if (!responseBubble) {
                            removeTypingIndicator();
                            responseBubble = addMessage('assistant', '');
                        }
                        aiResponseText += data.content;
                        responseBubble.innerHTML = aiResponseText;
                        messagesFeed.scrollTop = messagesFeed.scrollHeight;
                    } else if (data.type === 'done') {
                        // Done streaming - trigger Text-to-Speech
                        speakText(aiResponseText);
                    }
                } catch (e) {
                    // Skip malformed SSE lines
                }
            }
        }

        // Store AI response in history
        if (aiResponseText) {
            state.chatHistory.push({ role: 'assistant', content: aiResponseText });
        }

    } catch (err) {
        removeTypingIndicator();
        addMessage('assistant',
            `I'm sorry, I'm having trouble connecting right now. Please try again in a moment. ` +
            `If you're in crisis, please call <strong>988</strong> or text <strong>HOME to 741741</strong>.`
        );
        console.error('Streaming error:', err);
    } finally {
        state.isStreaming = false;
        userInput.disabled = false;
        sendBtn.disabled = false;
        micBtn.disabled = false;
        userInput.focus();
    }
}

// ============================================================
// Text-to-Speech (Web Speech API)
// ============================================================
function speakText(text) {
    if (!ttsToggle.checked) return;
    if (!('speechSynthesis' in window)) return;
    
    // Clean text for speech
    const cleanText = text.replace(/[*#]/g, '');
    const utterance = new SpeechSynthesisUtterance(cleanText);
    
    const voices = window.speechSynthesis.getVoices();
    const preferredVoice = voices.find(v => v.name.includes('Google US English') || v.name.includes('Samantha') || (v.lang === 'en-US' && v.name.includes('Female')));
    if (preferredVoice) utterance.voice = preferredVoice;
    
    utterance.rate = 0.95; // Slightly slower, calming pace
    utterance.pitch = 1.0;
    
    window.speechSynthesis.speak(utterance);
}

// Make sure voices are loaded
if ('speechSynthesis' in window) {
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}

// ============================================================
// Voice Recording (Speech-to-Text via Whisper)
// ============================================================
let mediaRecorder = null;
let audioChunks = [];

async function toggleRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        micBtn.classList.remove('recording');
        micBtn.innerHTML = '🔄'; 
        micBtn.disabled = true;
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            stream.getTracks().forEach(track => track.stop());
            await transcribeAudio(audioBlob);
        };

        mediaRecorder.start();
        micBtn.classList.add('recording');
    } catch (err) {
        console.error('Microphone access denied:', err);
        addMessage('assistant', "I don't have permission to access your microphone. Please enable it in your browser settings.");
    }
}

async function transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append('file', audioBlob, 'recording.webm');

    try {
        const response = await fetch('/api/audio/transcribe', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) throw new Error('Transcription failed');

        const data = await response.json();
        const text = data.text.trim();

        if (text) {
            userInput.value = text;
            userInput.style.height = 'auto';
            userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
            
            if (state.phase === 'chatting' || state._assessmentTextHandler) {
                sendBtn.click();
            }
        }
    } catch (err) {
        console.error('Transcription error:', err);
    } finally {
        micBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" class="mic-icon-svg">
                <path d="M12 2C10.3431 2 9 3.34315 9 5V11C9 12.6569 10.3431 14 12 14C13.6569 14 15 12.6569 15 11V5C15 3.34315 13.6569 2 12 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M19 10V11C19 14.866 15.866 18 12 18C8.13401 18 5 14.866 5 11V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M12 18V22" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M8 22H16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        `;
        micBtn.disabled = false;
    }
}

// ============================================================
// Emoji Helper
// ============================================================
function getEmotionEmoji(emotion) {
    const map = {
        joy: '😊', sadness: '😢', anger: '😠', fear: '😰',
        surprise: '😲', disgust: '😣', neutral: '😐',
    };
    return map[emotion] || '🔵';
}

// ============================================================
// Event Listeners
// ============================================================

// Send button
sendBtn.addEventListener('click', () => {
    // Stop any ongoing TTS before sending a new message
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    
    const text = userInput.value.trim();
    if (!text) return;

    if (state.phase === 'assessment' && state._assessmentTextHandler) {
        state._assessmentTextHandler(text);
    } else if (state.phase === 'chatting') {
        sendStreamingMessage(text);
    }

    userInput.value = '';
    userInput.style.height = 'auto';
});

// Enter to send (Shift+Enter for new line)
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendBtn.click();
    }
});

// Auto-resize textarea
userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
});

// Sidebar toggle
sidebarToggle.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
});

// Microphone button
micBtn.addEventListener('click', toggleRecording);

// ============================================================
// Initialize
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    startAssessment();
});
