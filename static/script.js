// Helper to append a styled message bubble to the chat container
function appendMessage(sender, text) {
  const chatBox = document.getElementById('chat-box');
  const row = document.createElement('div');
  row.classList.add('message-row', sender);

  if (sender === 'ai') {
    row.innerHTML = `
      <div class="ai-avatar">✨</div>
      <div class="bubble">${text}</div>
    `;
  } else {
    row.innerHTML = `
      <div class="bubble">${text}</div>
    `;
  }

  chatBox.appendChild(row);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// Helper to display animated typing dots while waiting for the backend
function showTypingIndicator() {
  const chatBox = document.getElementById('chat-box');
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

    if (response.ok) {
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
  }
}

// Listen for "Enter" key press on the input field
document.addEventListener('DOMContentLoaded', () => {
  const inputField = document.getElementById('user-input');
  if (inputField) {
    inputField.addEventListener('keypress', function (e) {
      if (e.key === 'Enter') {
        sendPrompt();
      }
    });
  }
});