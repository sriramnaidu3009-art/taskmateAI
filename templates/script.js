async function sendPrompt() {
    const inputField = document.getElementById('user-input');
    const chatBox = document.getElementById('chat-box');
    const sendBtn = document.getElementById('send-btn');

    const promptText = inputField.value.trim();

    if (!promptText) {
        alert("Please enter a prompt!");
        return;
    }

    // UI Feedback: Show loading state and disable button
    chatBox.innerText = "Thinking...";
    sendBtn.disabled = true;

    try {
        // Send POST request to your Flask backend route
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ prompt: promptText })
        });

        const data = await response.json();

        if (response.ok) {
            // Display the AI response
            chatBox.innerText = data.response;
        } else {
            chatBox.innerText = "Error: " + (data.error || "Failed to get response.");
        }
    } catch (error) {
        console.error("Fetch Error:", error);
        chatBox.innerText = "Error connecting to the server. Please check your backend.";
    } finally {
        // Re-enable button and reset input field
        sendBtn.disabled = false;
        inputField.value = '';
    }
}

// Allow sending the prompt by pressing the "Enter" key
document.addEventListener('DOMContentLoaded', () => {
    const inputField = document.getElementById('user-input');
    if (inputField) {
        inputField.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendPrompt();
            }
        });
    }
});