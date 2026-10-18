// Helper to safely append a styled message bubble to the chat container
function appendMessage(sender, text) {
  const chatBox = document.getElementById('chat-box');
  if (!chatBox) return;

  const row = document.createElement('div');
  row.classList.add('message-row', sender);

  if (sender === 'ai') {
    const avatar = document.createElement('div');
    avatar.className = 'ai-avatar';
    avatar.textContent = '✨';
    row.appendChild(avatar);
  }

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text; // Safely escapes HTML/special characters
  row.appendChild(bubble);

  chatBox.appendChild(row);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// Helper to display animated typing dots while waiting for the backend
function showTypingIndicator() {
  const chatBox = document.getElementById('chat-box');
  if (!chatBox) return;

  // Remove any existing indicator first to avoid duplicates
  removeTypingIndicator();

  const typingRow = document.createElement('div');
  typingRow.id = 'typing-indicator';
  typingRow.classList.add('message-row', 'ai');
  typingRow.innerHTML = `
    <div class="ai-avatar">✨</div>
    <div class="bubble">
      <div class="typing-dots">
        <span></span><span></span><span></span>
      </div>
    </div>
  `;
  chatBox.appendChild(typingRow);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// Helper to remove the typing dots
function removeTypingIndicator() {
  const indicator = document.getElementById('typing-indicator');
  if (indicator) {
    indicator.remove();
  }
}

async function sendPrompt() {
  const inputField = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  if (!inputField || !sendBtn) return;

  const promptText = inputField.value.trim();
  if (!promptText) {
    return;
  }

  // 1. Display user message bubble and clear input
  appendMessage('user', promptText);
  inputField.value = '';
  sendBtn.disabled = true;

  // 2. Display typing animation indicator
  showTypingIndicator();

  try {
    // 3. Send request to Flask backend
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ prompt: promptText })
    });

    const data = await response.json();

    // 4. Remove typing indicator once response arrives
    removeTypingIndicator();

    // Handle both success and backend error JSON structures (data.response or data.error)
    if (data.response) {
      appendMessage('ai', data.response);
    } else {
      appendMessage('ai', `Error: ${data.error || 'Failed to get response.'}`);
    }
  } catch (error) {
    console.error('Fetch Error:', error);
    removeTypingIndicator();
    appendMessage('ai', 'Error connecting to the server. Please check your backend.');
  } finally {
    sendBtn.disabled = false;
    inputField.focus();
  }
}

// Listen for "Enter" key press and Send button clicks
document.addEventListener('DOMContentLoaded', () => {
  const inputField = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');

  if (inputField) {
    inputField.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendPrompt();
      }
    });
  }

  if (sendBtn && !sendBtn.getAttribute('onclick')) {
    sendBtn.addEventListener('click', sendPrompt);
  }
});