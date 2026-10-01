(function () {
  const base = (window.CHATBOT_BASE || "/cs/chatbot").replace(/\/$/, "");
  const input = document.getElementById("chatInput");
  const homeInput = document.getElementById("homeInput");
  const sendBtn = document.getElementById("chatSend");
  const form = document.getElementById("chatForm");
  const homeForm = document.getElementById("homeForm");
  const messagesEl = document.getElementById("chatMessages");
  const starterEl = document.getElementById("chatStarter");
  const chatScreenEl = document.getElementById("chatScreen");
  const chatTopicLabel = document.getElementById("chatTopicLabel");
  const backBtn = document.getElementById("backBtn");
  const quickRepliesEl = document.getElementById("chatQuickReplies");
  const feedbackButtons = document.getElementById("feedbackButtons");
  const specializedOfferButtons = document.getElementById("specializedOfferButtons");
  const orderShippingChoiceButtons = document.getElementById("orderShippingChoiceButtons");
  const ringConfirmationDecisionButtons = document.getElementById(
    "ringConfirmationDecisionButtons"
  );
  const ringAddressOfferButtons = document.getElementById("ringAddressOfferButtons");
  const ringKbChoiceButtons = document.getElementById("ringKbChoiceButtons");
  const orderEmailReuseButtons = document.getElementById("orderEmailReuseButtons");
  const helpChoiceButtons = document.getElementById("helpChoiceButtons");
  const batteryHelpButtons = document.getElementById("batteryHelpButtons");
  const batteryAskNewButtons = document.getElementById("batteryAskNewButtons");
  const chatbotFeedbackPanel = document.getElementById("chatbotFeedbackPanel");
  const chatbotFeedbackInput = document.getElementById("chatbotFeedbackInput");
  const chatbotFeedbackSendBtn = document.getElementById("chatbotFeedbackSendBtn");
  const chatbotFeedbackSkipBtn = document.getElementById("chatbotFeedbackSkipBtn");
  const ratingPanel = document.getElementById("ratingPanel");
  const agentSwitchButtons = document.getElementById("agentSwitchButtons");
  const agentTopicRedirectButtons = document.getElementById("agentTopicRedirectButtons");
  const supportNameConfirmButtons = document.getElementById("supportNameConfirmButtons");
  const escalationButtons = document.getElementById("escalationButtons");
  const ratingButtons = document.getElementById("ratingButtons");
  const chatCard = document.querySelector(".velio-chat-card");
  const agentWaitHintsEl = document.getElementById("agentWaitHints");

  if (!input || !sendBtn || !form || !messagesEl) return;

  let sessionId = null;
  let sessionRequestId = 0;
  let busy = false;
  let thinkingEl = null;
  let localMessages = [];
  let clarifyOptions = [];
  let clarifyOtherMode = false;
  let currentTopic = "Chat with Velio";
  let currentAgent = "typical_question_agent";
  let currentPhase = "chatting";
  let agentWaitTimer = null;
  let agentWaitShown = false;
  let agentWaitHintTimers = [];
  let agentWaitHintCycleTimer = null;
  let thinkingPollTimer = null;
  let thinkingLabelEl = null;
  let lastThinkingLabel = "";

  const REPLY_DELAY_MIN_MS = 1000;
  const REPLY_DELAY_MAX_MS = 4000;
  const AGENT_WAIT_MS = 3000;
  const AGENT_WAIT_HINT_DELAY_MS = 2500;
  const THINKING_POLL_MS = 250;
  const DEFAULT_THINKING_LABEL = "Reading your message…";

  function guessThinkingLabel(_msg) {
    return DEFAULT_THINKING_LABEL;
  }

  const AGENT_WAIT_HINTS = [
    "We're checking your information in our customer records.",
    "Your information is confidential and is never shared outside Velia.",
    "We're retrieving the details you need — this will only take a moment.",
  ];

  function randomReplyDelayMs() {
    return (
      REPLY_DELAY_MIN_MS +
      Math.random() * (REPLY_DELAY_MAX_MS - REPLY_DELAY_MIN_MS)
    );
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function waitBeforeShowingReply() {
    await sleep(randomReplyDelayMs());
  }

  function syncSendState() {
    const disabled = busy || input.disabled || !input.value.trim();
    sendBtn.disabled = disabled;
  }

  function focusInputIfReady() {
    if (!input) return;
    if (chatScreenEl && chatScreenEl.hidden) return;
    if (input.disabled || input.dataset.phaseDisabled === "1") return;
    requestAnimationFrame(() => {
      if (!input.disabled && input.dataset.phaseDisabled !== "1") {
        input.focus();
      }
    });
  }

  function setBusy(on) {
    busy = on;
    input.disabled = on || input.dataset.phaseDisabled === "1";
    syncSendState();
    if (!on) {
      focusInputIfReady();
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function linkifyHtml(escapedText) {
    return escapedText.replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );
  }

  function protectEmailsForFormatting(text) {
    const emails = [];
    const emailPattern =
      /[A-Za-z0-9][A-Za-z0-9._+-]*(?:\*+[A-Za-z0-9._+-]*)?@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}/g;

    let protectedText = text.replace(
      /\*\*([A-Za-z0-9][A-Za-z0-9._+-]*(?:\*+[A-Za-z0-9._+-]*)?@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})\*\*/g,
      (_, email) => {
        const token = `@@VELIO_EMAIL_${emails.length}@@`;
        emails.push(email);
        return token;
      }
    );

    protectedText = protectedText.replace(emailPattern, (email) => {
      const token = `@@VELIO_EMAIL_${emails.length}@@`;
      emails.push(email);
      return token;
    });

    return { protectedText, emails };
  }

  function restoreProtectedEmails(text, emails) {
    return emails.reduce(
      (out, email, index) => out.replace(`@@VELIO_EMAIL_${index}@@`, email),
      text
    );
  }

  function formatBoldHtml(escapedText) {
    const { protectedText, emails } = protectEmailsForFormatting(escapedText);
    const bolded = protectedText.replace(/\*\*([^*]+?)\*\*/g, (match, inner) => {
      if (inner.includes("@")) return match;
      return `<strong>${inner}</strong>`;
    });
    return restoreProtectedEmails(bolded, emails);
  }

  function formatMessageHtml(text) {
    const escaped = escapeHtml(text).replace(/\n/g, "<br>");
    return linkifyHtml(formatBoldHtml(escaped));
  }

  function normalizeMessages(list) {
    return (list || [])
      .filter((m) => m && m.content)
      .map((m) => ({
        role: m.role === "user" ? "user" : "assistant",
        content: String(m.content),
        at: m.at || null,
      }));
  }

  function parseNumberedOptionsFromText(text) {
    const options = [];
    String(text || "")
      .split("\n")
      .forEach((line) => {
        const m = line.trim().match(/^(\d+)\.\s+(.+)$/);
        if (m) {
          options.push({ index: parseInt(m[1], 10), title: m[2].trim() });
        }
      });
    return options.length >= 2 ? options : [];
  }

  function resolveClarifyOptions(data) {
    const fromApi = (data && data.clarify_options) || [];
    if (fromApi.length > 0) {
      return fromApi
        .map((opt, i) => ({
          index: opt.index || i + 1,
          title: String(opt.title || "").trim(),
        }))
        .filter((opt) => opt.title);
    }
    const last = localMessages[localMessages.length - 1];
    if (last && last.role === "assistant") {
      return parseNumberedOptionsFromText(last.content);
    }
    return [];
  }

  function resetChat() {
    sessionRequestId += 1;
    sessionId = null;
    busy = false;
    thinkingEl = null;
    localMessages = [];
    clarifyOptions = [];
    clarifyOtherMode = false;
    currentTopic = "Chat with Velio";
    currentAgent = "typical_question_agent";
    currentPhase = "chatting";
    agentWaitShown = false;
    clearAgentWaitTimer();
    hideAgentWaitHints();

    hideThinking();
    removeClarifyInline();
    messagesEl.innerHTML = "";

    if (chatTopicLabel) chatTopicLabel.textContent = "Chat with Velio";
    if (input) {
      input.value = "";
      input.placeholder = "Ask Velio a question…";
      input.dataset.phaseDisabled = "0";
      input.disabled = false;
    }
    if (homeInput) homeInput.value = "";

    if (quickRepliesEl) quickRepliesEl.hidden = true;
    if (feedbackButtons) feedbackButtons.hidden = true;
    if (specializedOfferButtons) specializedOfferButtons.hidden = true;
    if (orderShippingChoiceButtons) orderShippingChoiceButtons.hidden = true;
    if (ringConfirmationDecisionButtons) ringConfirmationDecisionButtons.hidden = true;
    if (ringAddressOfferButtons) ringAddressOfferButtons.hidden = true;
    if (ringKbChoiceButtons) ringKbChoiceButtons.hidden = true;
    if (orderEmailReuseButtons) orderEmailReuseButtons.hidden = true;
    if (helpChoiceButtons) helpChoiceButtons.hidden = true;
    if (batteryHelpButtons) batteryHelpButtons.hidden = true;
    if (batteryAskNewButtons) batteryAskNewButtons.hidden = true;
    if (chatbotFeedbackPanel) chatbotFeedbackPanel.hidden = true;
    if (chatbotFeedbackInput) chatbotFeedbackInput.value = "";
    if (ratingPanel) ratingPanel.hidden = true;
    if (form) form.hidden = false;
    if (agentSwitchButtons) agentSwitchButtons.hidden = true;
    if (agentTopicRedirectButtons) agentTopicRedirectButtons.hidden = true;
    if (supportNameConfirmButtons) supportNameConfirmButtons.hidden = true;
    if (escalationButtons) escalationButtons.hidden = true;

    showHome();
    syncSendState();
  }

  function showHome() {
    if (starterEl) starterEl.hidden = false;
    if (chatScreenEl) chatScreenEl.hidden = true;
    if (chatCard) chatCard.classList.remove("velio-chat-card--conversation");
  }

  function showChat(topic) {
    if (topic) {
      currentTopic = topic;
      if (chatTopicLabel) chatTopicLabel.textContent = topic;
    }
    if (starterEl) starterEl.hidden = true;
    if (chatScreenEl) chatScreenEl.hidden = false;
    if (chatCard) chatCard.classList.add("velio-chat-card--conversation");
    focusInputIfReady();
  }

  function updateLayout(showStarter) {
    if (showStarter) {
      showHome();
    } else {
      showChat(currentTopic);
    }
  }

  function setChatStarted(started, topic) {
    if (started) {
      showChat(topic || currentTopic);
    } else {
      updateLayout(true);
    }
  }

  function scrollMessagesToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  const LEGACY_WAIT_MESSAGE =
    "This process is going to take a few seconds. Thanks for your patience.";

  function removeLegacyWaitMessages(messages) {
    return (messages || []).filter(
      (m) =>
        !(m.role === "assistant" && m.content === LEGACY_WAIT_MESSAGE)
    );
  }

  function appendBubble(role, text) {
    const isUser = role === "user";
    const wrap = document.createElement("div");
    wrap.className = `velio-msg velio-msg--${isUser ? "user" : "bot"}`;

    const avatar = document.createElement("div");
    avatar.className = "velio-msg-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = isUser ? "U" : "V";

    const bubble = document.createElement("div");
    bubble.className = "velio-msg-bubble";
    bubble.innerHTML = formatMessageHtml(text);

    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    scrollMessagesToBottom();
    return wrap;
  }

  function showThinking(userMsg) {
    hideThinking();
    const wrap = document.createElement("div");
    wrap.className = "velio-msg velio-msg--bot velio-msg--thinking";
    wrap.id = "chatThinking";
    wrap.setAttribute("aria-live", "polite");

    const avatar = document.createElement("div");
    avatar.className = "velio-msg-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "V";

    const bubble = document.createElement("div");
    bubble.className = "velio-msg-bubble velio-thinking-bubble";

    const initialLabel = guessThinkingLabel(userMsg);

    thinkingLabelEl = document.createElement("div");
    thinkingLabelEl.className = "velio-thinking-label velio-thinking-label--pulse";
    thinkingLabelEl.textContent = initialLabel;

    const typing = document.createElement("div");
    typing.className = "velio-typing-indicator";
    typing.setAttribute("aria-hidden", "true");
    typing.innerHTML =
      '<span class="velio-typing-dot"></span>' +
      '<span class="velio-typing-dot"></span>' +
      '<span class="velio-typing-dot"></span>';

    bubble.appendChild(thinkingLabelEl);
    bubble.appendChild(typing);
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    thinkingEl = wrap;
    lastThinkingLabel = initialLabel;
    scrollMessagesToBottom();
  }

  function updateThinkingLabel(text) {
    if (!thinkingLabelEl) return;
    const next = String(text || "").trim() || DEFAULT_THINKING_LABEL;
    if (next === lastThinkingLabel) return;
    lastThinkingLabel = next;
    thinkingLabelEl.textContent = next;
    thinkingLabelEl.classList.remove("velio-thinking-label--pulse");
    void thinkingLabelEl.offsetWidth;
    thinkingLabelEl.classList.add("velio-thinking-label--pulse");
  }

  function stopThinkingPoll() {
    if (thinkingPollTimer) {
      clearInterval(thinkingPollTimer);
      thinkingPollTimer = null;
    }
  }

  async function fetchThinkingStatus(targetSessionId) {
    if (!targetSessionId) return null;
    try {
      const res = await fetch(
        `${base}/api/thinking/${encodeURIComponent(targetSessionId)}`,
        { credentials: "include", cache: "no-store" }
      );
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    }
  }

  function startThinkingPoll(targetSessionId, sendRequestId) {
    stopThinkingPoll();
    if (!targetSessionId) return;

    async function poll() {
      if (sendRequestId !== sessionRequestId || !busy) return;
      const data = await fetchThinkingStatus(targetSessionId);
      if (!data || sendRequestId !== sessionRequestId || !busy) return;
      if (data.label) {
        hideAgentWaitHints();
        updateThinkingLabel(data.label);
      }
    }

    poll();
    thinkingPollTimer = setInterval(poll, THINKING_POLL_MS);
  }

  function hideThinking() {
    stopThinkingPoll();
    if (thinkingEl && thinkingEl.parentNode) {
      thinkingEl.parentNode.removeChild(thinkingEl);
    }
    thinkingEl = null;
    thinkingLabelEl = null;
    lastThinkingLabel = "";
    const stale = document.getElementById("chatThinking");
    if (stale && stale.parentNode) stale.parentNode.removeChild(stale);
  }

  function clearAgentWaitTimer() {
    if (agentWaitTimer) {
      clearTimeout(agentWaitTimer);
      agentWaitTimer = null;
    }
  }

  function clearAgentWaitHintTimers() {
    agentWaitHintTimers.forEach((timer) => clearTimeout(timer));
    agentWaitHintTimers = [];
    if (agentWaitHintCycleTimer) {
      clearInterval(agentWaitHintCycleTimer);
      agentWaitHintCycleTimer = null;
    }
  }

  function hideAgentWaitHints() {
    clearAgentWaitHintTimers();
    if (!agentWaitHintsEl) return;
    agentWaitHintsEl.hidden = true;
    agentWaitHintsEl.innerHTML = "";
  }

  function renderAgentWaitHint(cyclingText) {
    if (!agentWaitHintsEl) return;
    agentWaitHintsEl.innerHTML = "";

    const line = document.createElement("p");
    line.className = "velio-lookup-hint velio-lookup-hint--blink";
    line.textContent = cyclingText;
    agentWaitHintsEl.appendChild(line);
  }

  function showAgentWaitHints(sendRequestId) {
    if (!agentWaitHintsEl) return;
    hideAgentWaitHints();
    agentWaitHintsEl.hidden = false;

    let hintIndex = 0;

    function showNextHint() {
      if (sendRequestId !== sessionRequestId) return;
      if (!busy) return;
      renderAgentWaitHint(AGENT_WAIT_HINTS[hintIndex]);
      hintIndex = (hintIndex + 1) % AGENT_WAIT_HINTS.length;
    }

    showNextHint();
    agentWaitHintCycleTimer = setInterval(showNextHint, AGENT_WAIT_HINT_DELAY_MS);
  }

  function extractEmailFromText(msg) {
    const match = String(msg || "")
      .trim()
      .match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/i);
    return match ? match[0].toLowerCase() : null;
  }

  function parseLookupQuery(msg) {
    const trimmed = String(msg || "").trim();
    if (!trimmed) return null;

    const email = extractEmailFromText(trimmed);
    if (email) return email;

    const compact = trimmed.replace(/\s/g, "");
    if (/^\d{5,}$/.test(compact)) return compact;

    if (
      /^[A-Za-z0-9#][A-Za-z0-9#\s\-_.]{2,}$/.test(trimmed) &&
      trimmed.split(/\s+/).length <= 8
    ) {
      return trimmed;
    }

    return null;
  }

  function isFlowControlMessage(msg) {
    const trimmed = String(msg || "").trim();
    const lowered = trimmed.toLowerCase();
    if (/^switch to /i.test(trimmed)) return true;
    if (lowered === "continue with human support") return true;
    const flowMessages = new Set([
      "yes",
      "no",
      "yes, it helped",
      "ask a new question",
      "pass to human agent",
      "request support ticket",
      "this helped",
      "check battery health",
      "track my order",
      "change shipping address",
      "change address",
      "address change",
      "ring confirmation",
      "confirm",
      "change",
      "yes, create a ticket",
      "no thanks",
      "yes, same email",
      "use a different email",
      "1",
      "2",
      "3",
      "4",
      "5",
    ]);
    return flowMessages.has(lowered);
  }

  function shouldShowSlowResponseHints(msg) {
    const trimmed = String(msg || "").trim();
    if (!trimmed) return false;
    if (isFlowControlMessage(trimmed)) return false;
    if (currentPhase === "awaiting_agent_switch") return false;
    if (currentPhase === "awaiting_agent_topic_redirect") return false;
    if (currentPhase === "awaiting_support_name_confirm") return false;
    return true;
  }

  function scheduleAgentWaitMessage(sendRequestId, msg) {
    clearAgentWaitTimer();
    hideAgentWaitHints();
    if (!shouldShowSlowResponseHints(msg)) return;

    agentWaitShown = true;
    showAgentWaitHints(sendRequestId);
  }

  function removeClarifyInline() {
    const existing = messagesEl.querySelector(".velio-clarify-inline");
    if (existing && existing.parentNode) {
      existing.parentNode.removeChild(existing);
    }
  }

  function enableClarifyOtherInput() {
    clarifyOtherMode = true;
    removeClarifyInline();
    input.dataset.phaseDisabled = "0";
    input.disabled = busy;
    input.placeholder = "Describe your issue in your own words...";
    input.focus();
    syncSendState();
  }

  function renderSpecializedOfferButtons(options) {
    if (!specializedOfferButtons) return;
    specializedOfferButtons.innerHTML = "";
    const items = Array.isArray(options) && options.length
      ? options
      : [
          { label: "Check battery health", send: "Check battery health" },
          { label: "Track my order", send: "Track my order" },
          {
            label: "Ask a new question",
            send: "Ask a new question",
          },
        ];

    items.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "velio-chip-btn";
      if (opt.muted || String(opt.id || "") === "human") {
        btn.classList.add("velio-chip-btn--muted");
      }
      btn.textContent = opt.label || opt.send || "";
      btn.setAttribute("data-send", opt.send || opt.label || "");
      specializedOfferButtons.appendChild(btn);
    });
  }

  function hasQuickReplyChoices(ui, showClarify, clarifyCount) {
    if (!ui) return false;
    return (
      !!ui.show_feedback_buttons ||
      !!ui.show_kb_guided_buttons ||
      !!ui.show_specialized_offer_buttons ||
      !!ui.show_order_shipping_choice_buttons ||
      !!ui.show_ring_confirmation_decision_buttons ||
      !!ui.show_ring_address_offer_buttons ||
      !!ui.show_ring_kb_choice_buttons ||
      !!ui.show_order_email_reuse_buttons ||
      !!ui.show_agent_topic_redirect_buttons ||
      !!ui.show_support_name_confirm_buttons ||
      !!ui.show_help_choice_buttons ||
      !!ui.show_battery_help_buttons ||
      !!ui.show_battery_ask_new_button ||
      !!ui.show_agent_switch_buttons ||
      !!ui.show_escalation_buttons ||
      !!ui.show_rating_hint ||
      (showClarify && clarifyCount > 0 && !clarifyOtherMode)
    );
  }

  function renderClarifyOptions(options) {
    removeClarifyInline();
    if (!options || !options.length) return;

    const wrap = document.createElement("div");
    wrap.className = "velio-msg velio-msg--bot velio-clarify-inline";
    const panel = document.createElement("div");
    panel.className = "velio-clarify-panel";
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", "Choose an option");

    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "velio-clarify-chip";
      btn.innerHTML =
        '<span class="velio-clarify-chip-num">' +
        escapeHtml(String(opt.index)) +
        ".</span>" +
        escapeHtml(opt.title);
      btn.addEventListener("click", () => {
        sendUserMessage(opt.title);
      });
      panel.appendChild(btn);
    });

    const otherBtn = document.createElement("button");
    otherBtn.type = "button";
    otherBtn.className = "velio-clarify-chip velio-clarify-chip--other";
    otherBtn.textContent = "Other";
    otherBtn.addEventListener("click", enableClarifyOtherInput);
    panel.appendChild(otherBtn);

    wrap.appendChild(panel);
    messagesEl.appendChild(wrap);
    scrollMessagesToBottom();
  }

  function renderAgentTopicRedirectButtons(choices) {
    if (!agentTopicRedirectButtons) return;
    agentTopicRedirectButtons.innerHTML = "";
    const list = Array.isArray(choices) ? choices : [];
    if (!list.length) {
      agentTopicRedirectButtons.hidden = true;
      return;
    }
    list.forEach((choice, index) => {
      const label = choice && choice.label ? String(choice.label) : "";
      const send = choice && choice.send ? String(choice.send) : label;
      if (!send) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        index === 0
          ? "velio-chip-btn"
          : "velio-chip-btn velio-chip-btn--muted";
      btn.textContent = label || send;
      btn.addEventListener("click", () => {
        if (choice && choice.id === "switch_agent" && label) {
          setChatStarted(true, label.replace(/^Go to /i, ""));
        }
        sendUserMessage(send);
      });
      agentTopicRedirectButtons.appendChild(btn);
    });
    agentTopicRedirectButtons.hidden = false;
  }

  function renderAgentSwitchButtons(agentSwitch) {
    if (!agentSwitchButtons) return;
    agentSwitchButtons.innerHTML = "";
    const title = agentSwitch && agentSwitch.title ? String(agentSwitch.title) : "";
    if (!title) {
      agentSwitchButtons.hidden = true;
      return;
    }

    const switchBtn = document.createElement("button");
    switchBtn.type = "button";
    switchBtn.className = "velio-chip-btn";
    switchBtn.textContent = `Switch to ${title}`;
    switchBtn.addEventListener("click", () => {
      setChatStarted(true, title);
      if (agentSwitch.agent) currentAgent = agentSwitch.agent;
      sendUserMessage(`Switch to ${title}`);
    });
    agentSwitchButtons.appendChild(switchBtn);

    const continueBtn = document.createElement("button");
    continueBtn.type = "button";
    continueBtn.className = "velio-chip-btn velio-chip-btn--muted";
    continueBtn.textContent = "Continue with human support";
    continueBtn.addEventListener("click", () => {
      sendUserMessage("Continue with human support");
    });
    agentSwitchButtons.appendChild(continueBtn);
    agentSwitchButtons.hidden = false;
  }

  function hideQuickReplies() {
    if (quickRepliesEl) quickRepliesEl.hidden = true;
    if (feedbackButtons) feedbackButtons.hidden = true;
    if (specializedOfferButtons) specializedOfferButtons.hidden = true;
    if (orderShippingChoiceButtons) orderShippingChoiceButtons.hidden = true;
    if (ringConfirmationDecisionButtons) ringConfirmationDecisionButtons.hidden = true;
    if (ringAddressOfferButtons) ringAddressOfferButtons.hidden = true;
    if (ringKbChoiceButtons) ringKbChoiceButtons.hidden = true;
    if (orderEmailReuseButtons) orderEmailReuseButtons.hidden = true;
    if (helpChoiceButtons) helpChoiceButtons.hidden = true;
    if (batteryHelpButtons) batteryHelpButtons.hidden = true;
    if (batteryAskNewButtons) batteryAskNewButtons.hidden = true;
    if (chatbotFeedbackPanel) chatbotFeedbackPanel.hidden = true;
    if (chatbotFeedbackInput) chatbotFeedbackInput.value = "";
    if (ratingPanel) ratingPanel.hidden = true;
    if (form) form.hidden = false;
    if (agentSwitchButtons) agentSwitchButtons.hidden = true;
    if (agentTopicRedirectButtons) agentTopicRedirectButtons.hidden = true;
    if (supportNameConfirmButtons) supportNameConfirmButtons.hidden = true;
    if (escalationButtons) escalationButtons.hidden = true;
  }

  function updateFeedbackButtonLabels() {
    if (!feedbackButtons) return;
    const yesBtn = document.getElementById("feedbackYesBtn");
    const askBtn = document.getElementById("feedbackAskBtn");
    const humanBtn = document.getElementById("feedbackHumanBtn");
    if (!yesBtn || !askBtn || !humanBtn) return;

    if (currentPhase === "awaiting_kb_guided_step" || currentPhase === "awaiting_kb_confirm") {
      yesBtn.textContent = "Yes";
      yesBtn.setAttribute("data-send", "Yes");
      askBtn.textContent = "No";
      askBtn.setAttribute("data-send", "No");
      askBtn.hidden = false;
      humanBtn.hidden = true;
      return;
    }

    humanBtn.hidden = false;
    askBtn.hidden = false;
    askBtn.textContent = "Ask a new question";
    askBtn.setAttribute("data-send", "Ask a new question");
    humanBtn.textContent = "Pass to Human Agent";
    humanBtn.setAttribute("data-send", "Pass to Human Agent");

    const isOrderShipping =
      currentAgent === "order_shipping_agent" ||
      /order\s*&\s*shipping/i.test(currentTopic || "");
    if (isOrderShipping) {
      yesBtn.textContent = "Yes, it helped";
      yesBtn.setAttribute("data-send", "Yes, it helped");
    } else {
      yesBtn.textContent = "Yes, it helped";
      yesBtn.setAttribute("data-send", "Yes");
    }
  }

  function applyUi(ui, data) {
    if (!ui) return;
    if (data && data.agent) {
      currentAgent = data.agent;
    }
    if (data && data.phase) {
      currentPhase = data.phase;
    }
    if (data && data.topic) {
      currentTopic = data.topic;
      if (chatTopicLabel) chatTopicLabel.textContent = data.topic;
    }
    const showStarter =
      !!ui.show_starter && !busy && localMessages.length === 0;
    updateLayout(showStarter);

    const showClarify = !!ui.show_clarify_options;
    clarifyOptions = showClarify ? resolveClarifyOptions(data) : [];
    if (showClarify) {
      clarifyOtherMode = false;
    }
    renderClarifyOptions(clarifyOptions);

    const choicesVisible =
      hasQuickReplyChoices(ui, showClarify, clarifyOptions.length) &&
      !ui.specialized_offer_free_text &&
      !ui.chatbot_feedback_free_text;
    const blockInput = ui.input_disabled || choicesVisible;
    input.dataset.phaseDisabled = blockInput ? "1" : "0";
    input.disabled = busy || blockInput;

    const showQuickReplies =
      ui.show_feedback_buttons ||
      ui.show_kb_guided_buttons ||
      ui.show_specialized_offer_buttons ||
      ui.show_order_shipping_choice_buttons ||
      ui.show_ring_confirmation_decision_buttons ||
      ui.show_ring_address_offer_buttons ||
      ui.show_ring_kb_choice_buttons ||
      ui.show_order_email_reuse_buttons ||
      ui.show_agent_topic_redirect_buttons ||
      ui.show_support_name_confirm_buttons ||
      ui.show_help_choice_buttons ||
      ui.show_battery_help_buttons ||
      ui.show_battery_ask_new_button ||
      ui.show_agent_switch_buttons ||
      ui.show_escalation_buttons ||
      ui.show_rating_hint;

    if (quickRepliesEl) {
      quickRepliesEl.hidden = !showQuickReplies;
    }
    if (feedbackButtons) {
      feedbackButtons.hidden = busy || !(ui.show_feedback_buttons || ui.show_kb_guided_buttons);
      if (ui.show_feedback_buttons || ui.show_kb_guided_buttons) {
        updateFeedbackButtonLabels();
      }
    }
    if (specializedOfferButtons) {
      specializedOfferButtons.hidden = busy || !ui.show_specialized_offer_buttons;
      if (ui.show_specialized_offer_buttons) {
        renderSpecializedOfferButtons(
          data && data.specialized_offer_options ? data.specialized_offer_options : []
        );
      }
    }
    if (orderShippingChoiceButtons) {
      orderShippingChoiceButtons.hidden =
        busy || !ui.show_order_shipping_choice_buttons;
    }
    if (ringConfirmationDecisionButtons) {
      ringConfirmationDecisionButtons.hidden =
        busy || !ui.show_ring_confirmation_decision_buttons;
    }
    if (ringAddressOfferButtons) {
      ringAddressOfferButtons.hidden =
        busy || !ui.show_ring_address_offer_buttons;
    }
    if (ringKbChoiceButtons) {
      ringKbChoiceButtons.hidden = busy || !ui.show_ring_kb_choice_buttons;
    }
    if (orderEmailReuseButtons) {
      orderEmailReuseButtons.hidden = busy || !ui.show_order_email_reuse_buttons;
    }
    if (agentTopicRedirectButtons) {
      agentTopicRedirectButtons.hidden =
        busy || !ui.show_agent_topic_redirect_buttons;
      if (ui.show_agent_topic_redirect_buttons) {
        renderAgentTopicRedirectButtons(
          data && data.agent_topic_redirect_choices
            ? data.agent_topic_redirect_choices
            : []
        );
      }
    }
    if (supportNameConfirmButtons) {
      supportNameConfirmButtons.hidden =
        busy || !ui.show_support_name_confirm_buttons;
    }
    if (helpChoiceButtons) {
      helpChoiceButtons.hidden = busy || !ui.show_help_choice_buttons;
    }
    if (batteryHelpButtons) {
      batteryHelpButtons.hidden = busy || !ui.show_battery_help_buttons;
    }
    if (batteryAskNewButtons) {
      batteryAskNewButtons.hidden = busy || !ui.show_battery_ask_new_button;
    }
    if (!busy && ui.show_agent_switch_buttons) {
      renderAgentSwitchButtons(data && data.agent_switch);
    } else if (agentSwitchButtons) {
      agentSwitchButtons.hidden = true;
    }
    if (escalationButtons) {
      escalationButtons.hidden = busy || !ui.show_escalation_buttons;
    }
    const inFeedbackMode = !!ui.chatbot_feedback_free_text;
    if (chatbotFeedbackPanel) {
      chatbotFeedbackPanel.hidden = busy || !inFeedbackMode;
    }
    if (form) {
      form.hidden = inFeedbackMode;
    }
    if (inFeedbackMode && chatbotFeedbackInput && !busy) {
      window.requestAnimationFrame(() => chatbotFeedbackInput.focus());
    }

    if (ratingPanel) {
      const showRating = !busy && !!ui.show_rating_hint;
      ratingPanel.hidden = !showRating;
      if (ratingButtons) {
        ratingButtons.hidden = !showRating;
      }
    }

    if (blockInput && showClarify) {
      input.placeholder = "Choose an option or tap Other";
    } else if (blockInput && choicesVisible) {
      input.placeholder = "Choose an option above";
    } else if (!clarifyOtherMode && !inFeedbackMode) {
      input.placeholder = "Ask Velio a question…";
    }

    syncSendState();
    if (!blockInput && !busy) {
      focusInputIfReady();
    }
  }

  function renderMessages(messages) {
    hideThinking();
    removeClarifyInline();
    messagesEl.innerHTML = "";
    const list = normalizeMessages(messages);
    list.forEach((m) => {
      const role = m.role === "user" ? "user" : "bot";
      appendBubble(role, m.content);
    });
    if (clarifyOptions.length > 0) {
      renderClarifyOptions(clarifyOptions);
    }
  }

  function syncLocalFromServer(serverMessages, replyText) {
    const server = removeLegacyWaitMessages(normalizeMessages(serverMessages));

    if (server.length >= localMessages.length) {
      localMessages = server;
    } else if (server.length > 0) {
      localMessages = server;
    } else if (replyText) {
      const last = localMessages[localMessages.length - 1];
      if (!last || last.role !== "assistant" || last.content !== replyText) {
        localMessages.push({ role: "assistant", content: replyText });
      }
    }

    localMessages = removeLegacyWaitMessages(localMessages);
  }

  async function api(path, body) {
    const res = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    return data;
  }

  async function ensureSession() {
    if (sessionId) return sessionId;

    const requestId = sessionRequestId;
    const hadLocalMessages = localMessages.length > 0;
    const data = await api("/api/session", {
      agent: currentAgent,
      topic: currentTopic,
    });

    if (requestId !== sessionRequestId) {
      if (sessionId) return sessionId;
      return ensureSession();
    }

    sessionId = data.session_id;
    const serverMessages = normalizeMessages(data.messages);

    if (hadLocalMessages) {
      applyUi({ ...(data.ui || {}), show_starter: false }, data);
    } else {
      localMessages = serverMessages;
      applyUi(data.ui, data);
      if (localMessages.length) {
        renderMessages(localMessages);
        showChat(currentTopic);
      }
    }
    return sessionId;
  }

  function openStreamBubble() {
    hideThinking();
    const wrap = appendBubble("bot", "");
    const bubble = wrap.querySelector(".velio-msg-bubble");
    if (bubble) bubble.classList.add("is-streaming");
    return bubble;
  }

  function paintStreamBubble(bubble, text) {
    if (!bubble) return;
    bubble.innerHTML = formatMessageHtml(text);
    scrollMessagesToBottom();
  }

  async function streamChatMessage(body, sendRequestId) {
    const res = await fetch(`${base}/api/message/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      credentials: "include",
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    if (!res.body) {
      throw new Error("Streaming is not available in this browser.");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    let streamText = "";
    let bubble = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (sendRequestId !== sessionRequestId) {
        await reader.cancel();
        return {};
      }
      pending += decoder.decode(value, { stream: true });
      const frames = pending.split("\n\n");
      pending = frames.pop() || "";
      for (const frame of frames) {
        const line = frame
          .split("\n")
          .map((part) => part.trim())
          .find((part) => part.startsWith("data:"));
        if (!line) continue;
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch (_) {
          continue;
        }
        if (event.type === "start") {
          streamText = "";
          bubble = openStreamBubble();
        } else if (event.type === "reset") {
          streamText = "";
          paintStreamBubble(bubble, "");
        } else if (event.type === "token") {
          if (!bubble) bubble = openStreamBubble();
          streamText += event.text || "";
          paintStreamBubble(bubble, streamText);
        } else if (event.type === "error") {
          throw new Error(event.error || "Something went wrong. Please try again.");
        } else if (event.type === "done") {
          return event.result || {};
        }
      }
    }
    throw new Error("The reply stream ended before it finished.");
  }

  async function sendUserMessage(text, topic) {
    const msg = String(text || "").trim();
    if (!msg || busy) return;

    setChatStarted(true, topic || currentTopic);
    clarifyOtherMode = false;
    removeClarifyInline();
    localMessages.push({ role: "user", content: msg });
    renderMessages(localMessages);
    hideQuickReplies();
    showThinking(msg);
    setBusy(true);
    agentWaitShown = false;
    hideAgentWaitHints();
    const sendRequestId = sessionRequestId;

    try {
      await ensureSession();
      if (!sessionId) {
        throw new Error("Could not start a chat session. Please try again.");
      }
      startThinkingPoll(sessionId, sendRequestId);
      const data = await streamChatMessage(
        {
          session_id: sessionId,
          message: msg,
          agent: currentAgent,
          topic: currentTopic,
        },
        sendRequestId
      );
      if (sendRequestId !== sessionRequestId) return;

      hideThinking();
      sessionId = data.session_id || sessionId;
      syncLocalFromServer(data.messages, data.reply);
      renderMessages(localMessages);
      setBusy(false);
      applyUi(data.ui, data);
    } catch (err) {
      if (sendRequestId !== sessionRequestId) return;
      hideThinking();
      const errText = err.message || "Something went wrong. Please try again.";
      localMessages.push({ role: "assistant", content: errText });
      renderMessages(localMessages);
    } finally {
      clearAgentWaitTimer();
      hideAgentWaitHints();
      agentWaitShown = false;
      setBusy(false);
    }
  }

  function submitChatbotFeedback() {
    if (!chatbotFeedbackInput || busy) return;
    const text = chatbotFeedbackInput.value.trim();
    chatbotFeedbackInput.value = "";
    sendUserMessage(text || "Skip");
  }

  if (chatbotFeedbackSendBtn) {
    chatbotFeedbackSendBtn.addEventListener("click", () => {
      submitChatbotFeedback();
    });
  }

  if (chatbotFeedbackSkipBtn) {
    chatbotFeedbackSkipBtn.addEventListener("click", () => {
      if (busy) return;
      if (chatbotFeedbackInput) chatbotFeedbackInput.value = "";
      sendUserMessage("Skip");
    });
  }

  if (chatbotFeedbackInput) {
    chatbotFeedbackInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        submitChatbotFeedback();
      }
    });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (input.disabled || input.dataset.phaseDisabled === "1") return;
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    input.placeholder = "Ask Velio a question…";
    syncSendState();
    sendUserMessage(msg);
  });

  if (homeForm && homeInput) {
    homeForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const msg = homeInput.value.trim();
      if (!msg) return;
      homeInput.value = "";
      sendUserMessage(msg, "Chat with Velio");
    });
  }

  input.addEventListener("input", syncSendState);

  if (backBtn) {
    backBtn.addEventListener("click", () => {
      resetChat();
    });
  }

  document.querySelectorAll(".velio-category-btn[data-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const topic = btn.getAttribute("data-topic") || "Chat with Velio";
      const prompt = btn.getAttribute("data-prompt") || "";
      currentAgent = btn.getAttribute("data-agent") || "typical_question_agent";
      sessionRequestId += 1;
      sessionId = null;
      sendUserMessage(prompt, topic);
    });
  });

  document.querySelectorAll(".velio-quick-btn[data-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const topic = btn.getAttribute("data-topic") || "Chat with Velio";
      const prompt = btn.getAttribute("data-prompt") || "";
      if (btn.getAttribute("data-agent")) {
        currentAgent = btn.getAttribute("data-agent") || "typical_question_agent";
        sessionRequestId += 1;
        sessionId = null;
      }
      sendUserMessage(prompt, topic);
    });
  });

  if (quickRepliesEl) {
    quickRepliesEl.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-send]");
      if (!btn || btn.disabled || busy) return;
      const msg = btn.getAttribute("data-send");
      if (msg) sendUserMessage(msg);
    });
  }

  ensureSession().catch(() => {});
  syncSendState();
})();
