const OVERPURCHASE_EXISTING_THRESHOLD = 30;
const OVERPURCHASE_ADD_THRESHOLD = 20;
const OVERPURCHASE_PROJECTED_THRESHOLD = 80;

const state = {
    items: [],
    editingOriginal: null,
};

const itemTemplate = document.getElementById("item-template");
const inventoryList = document.getElementById("inventory-list");
const addForm = document.getElementById("add-form");
const askForm = document.getElementById("ask-form");
const answerEl = document.getElementById("answer");
const sustainabilityReportBtn = document.getElementById("sustainability-report-btn");
const editForm = document.getElementById("edit-form");
const cancelEditBtn = document.getElementById("cancel-edit");
const neverExpiryCheckbox = document.getElementById("never-expiry");
const expiryInput = document.getElementById("expiry");
const editNeverExpiryCheckbox = document.getElementById("edit-never-expiry");
const editExpiryInput = document.getElementById("edit-expiry");
const overpurchaseModal = document.getElementById("overpurchase-modal");
const overpurchaseMessage = document.getElementById("overpurchase-message");
const overpurchaseConfirmBtn = document.getElementById("overpurchase-confirm");
const overpurchaseCancelBtn = document.getElementById("overpurchase-cancel");
let overpurchaseResolver = null;

init().catch((err) => {
    setAnswer(`Failed to initialize UI: ${err.message}`);
});

neverExpiryCheckbox.addEventListener("change", () => {
    expiryInput.disabled = neverExpiryCheckbox.checked;
    if (neverExpiryCheckbox.checked) {
        expiryInput.value = "";
    }
});

editNeverExpiryCheckbox.addEventListener("change", () => {
    editExpiryInput.disabled = editNeverExpiryCheckbox.checked;
    if (editNeverExpiryCheckbox.checked) {
        editExpiryInput.value = "";
    }
});

addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(addForm);

    const next = {
        name: String(form.get("name") || "").trim(),
        category: String(form.get("category") || "General").trim() || "General",
        quantity: Number(form.get("quantity") || 0),
        expiry_date: neverExpiryCheckbox.checked ? "never" : String(form.get("expiry") || "").trim(),
        usage_unit: "day",
        usage_history: [],
    };

    if (!next.name) {
        setAnswer("Please add an item name.");
        return;
    }
    if (!Number.isInteger(next.quantity) || next.quantity <= 0) {
        setAnswer("Please enter a quantity greater than 0.");
        return;
    }

    const allow = await guardOverpurchase(next.name, next.quantity);
    if (!allow) {
        setAnswer("Add cancelled to avoid overpurchase.");
        return;
    }

    const result = await apiRequest("/api/items", {
        method: "POST",
        body: JSON.stringify(next),
    });
    if (!result.ok) {
        setAnswer(result.error || "Could not add item.");
        return;
    }

    await refreshItems();
    addForm.reset();
    document.getElementById("quantity").value = "1";
    document.getElementById("category").value = "General";
    document.getElementById("usage-unit").value = "day";
    neverExpiryCheckbox.checked = false;
    expiryInput.disabled = false;
    setAnswer(`Added ${next.name}.`);
});

askForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = String(document.getElementById("question").value || "").trim();
    if (!question) return;

    const reply = await handleQuestion(question);
    setAnswer(reply);
    render();
});

editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.editingOriginal) return;

    const form = new FormData(editForm);
    const nextName = String(form.get("name") || "").trim();
    const nextCategory = String(form.get("category") || "General").trim() || "General";
    const nextQuantity = Number(form.get("quantity") || 0);
    const nextExpiry = editNeverExpiryCheckbox.checked ? "never" : String(form.get("expiry") || "").trim();

    if (!nextName) {
        setAnswer("Item name cannot be empty.");
        return;
    }
    if (!Number.isInteger(nextQuantity) || nextQuantity < 0) {
        setAnswer("Quantity must be 0 or greater.");
        return;
    }

    const result = await apiRequest("/api/items", {
        method: "PATCH",
        body: JSON.stringify({
            original_name: state.editingOriginal.name,
            original_expiry: state.editingOriginal.expiry_date,
            name: nextName,
            category: nextCategory,
            quantity: nextQuantity,
            expiry_date: nextExpiry,
        }),
    });
    if (!result.ok) {
        setAnswer(result.error || "Could not update item.");
        return;
    }

    await refreshItems();
    closeEditor();
    setAnswer(`Updated ${nextName}. Changes are now saved to backend JSON.`);
});

cancelEditBtn.addEventListener("click", () => {
    closeEditor();
    setAnswer("Edit cancelled.");
});

overpurchaseConfirmBtn.addEventListener("click", () => {
    settleOverpurchaseDialog(true);
});

overpurchaseCancelBtn.addEventListener("click", () => {
    settleOverpurchaseDialog(false);
});

overpurchaseModal.addEventListener("click", (event) => {
    if (event.target === overpurchaseModal) {
        settleOverpurchaseDialog(false);
    }
});

sustainabilityReportBtn.addEventListener("click", async () => {
    sustainabilityReportBtn.disabled = true;
    setAnswer("Generating sustainability insights...");

    const response = await apiRequest("/api/reports/sustainability");
    sustainabilityReportBtn.disabled = false;

    if (!response.ok) {
        setAnswer(response.error || "Could not generate sustainability report.");
        return;
    }

    const report = response.data?.report;
    setAnswer(formatSustainabilityReport(report));
});

async function init() {
    const health = await apiRequest("/api/health");
    if (!health.ok) {
        setAnswer("Backend API unavailable. Start with: python app/web.py");
        return;
    }
    await refreshItems();
}

async function refreshItems() {
    const response = await apiRequest("/api/items");
    if (!response.ok) {
        setAnswer(response.error || "Failed to load inventory from backend.");
        return;
    }

    const items = Array.isArray(response.data?.items) ? response.data.items : [];
    state.items = items.map((item, index) => ({ ...item, _uiKey: makeItemKey(item, index) }));
    render();
}

function render() {
    inventoryList.innerHTML = "";

    if (!state.items.length) {
        inventoryList.textContent = "No items yet. Add your first one above.";
        return;
    }

    const sorted = [...state.items].sort((a, b) => String(a.name).localeCompare(String(b.name)));
    for (const item of sorted) {
        const node = itemTemplate.content.firstElementChild.cloneNode(true);
        node.dataset.itemKey = item._uiKey;
        node.classList.add(getStockClass(Number(item.quantity || 0)));
        node.querySelector(".item-name").textContent = item.name;

        const expiryLabel = item.expiry_date === "never"
            ? "Never expires"
            : item.expiry_date
                ? `Expires ${item.expiry_date}`
                : "No expiry";
        const consumedToday = getConsumedToday(item);
        const meta = `${item.category || "General"} · ${expiryLabel} · usage/${item.usage_unit || "day"} · consumed today: ${consumedToday}`;
        node.querySelector(".meta").textContent = meta;
        node.querySelector(".qty").textContent = `${item.quantity} in stock`;
        node.querySelector(".edit-item-btn").addEventListener("click", () => openEditor(item));
        inventoryList.appendChild(node);
    }
}

function makeItemKey(item, index) {
    return `${String(item.name || "").toLowerCase()}::${String(item.expiry_date || "").toLowerCase()}::${index}`;
}

function getStockClass(quantity) {
    if (quantity < 5) return "stock-low";
    if (quantity <= 30) return "stock-mid";
    return "stock-high";
}

function getConsumedToday(item) {
    const history = Array.isArray(item.usage_history) ? item.usage_history : [];
    const dates = Array.isArray(item.usage_history_dates) ? item.usage_history_dates : [];
    const today = new Date().toISOString().slice(0, 10);
    let total = 0;

    for (let i = 0; i < history.length; i += 1) {
        if (String(dates[i] || "") === today) {
            total += Number(history[i] || 0);
        }
    }
    return total;
}

function openEditor(item) {
    state.editingOriginal = {
        name: String(item.name || ""),
        expiry_date: String(item.expiry_date || ""),
    };

    document.getElementById("edit-name").value = item.name || "";
    document.getElementById("edit-category").value = item.category || "General";
    document.getElementById("edit-quantity").value = String(item.quantity || 0);

    if (item.expiry_date === "never") {
        editNeverExpiryCheckbox.checked = true;
        editExpiryInput.value = "";
        editExpiryInput.disabled = true;
    } else {
        editNeverExpiryCheckbox.checked = false;
        editExpiryInput.value = item.expiry_date || "";
        editExpiryInput.disabled = false;
    }
    editForm.classList.remove("hidden");
}

function closeEditor() {
    state.editingOriginal = null;
    editForm.classList.add("hidden");
    editForm.reset();
    editNeverExpiryCheckbox.checked = false;
    editExpiryInput.disabled = false;
}

function setAnswer(text) {
    answerEl.textContent = text;
}

function normalizeName(value) {
    return String(value || "").trim().toLowerCase();
}

function findItem(name) {
    const key = normalizeName(name);
    return state.items.find((item) => normalizeName(item.name) === key);
}

async function guardOverpurchase(name, quantityToAdd) {
    const existing = findItem(name);
    if (!existing) return true;

    const currentQty = Number(existing.quantity || 0);
    const projectedQty = currentQty + quantityToAdd;
    const likelyOverpurchase =
        (currentQty >= OVERPURCHASE_EXISTING_THRESHOLD && quantityToAdd >= OVERPURCHASE_ADD_THRESHOLD)
        || projectedQty > OVERPURCHASE_PROJECTED_THRESHOLD;

    if (!likelyOverpurchase) return true;

    const msg = `${existing.name} already has ${currentQty} in stock. Adding ${quantityToAdd} would raise it to ${projectedQty}. Continue?`;
    return openOverpurchaseDialog(msg);
}

function openOverpurchaseDialog(message) {
    overpurchaseMessage.textContent = message;
    overpurchaseModal.classList.remove("hidden");
    overpurchaseConfirmBtn.focus();

    return new Promise((resolve) => {
        overpurchaseResolver = resolve;
    });
}

function settleOverpurchaseDialog(value) {
    if (typeof overpurchaseResolver === "function") {
        overpurchaseResolver(value);
    }
    overpurchaseResolver = null;
    overpurchaseModal.classList.add("hidden");
}

async function consume(name, quantity) {
    const result = await apiRequest("/api/consume", {
        method: "POST",
        body: JSON.stringify({ name, quantity }),
    });
    if (!result.ok) {
        return result.error || "Could not consume item.";
    }

    await refreshItems();
    return `Consumed ${quantity} ${name}.`;
}

async function throwAway(name, quantity) {
    const result = await apiRequest("/api/throw-away", {
        method: "POST",
        body: JSON.stringify({ name, quantity }),
    });
    if (!result.ok) {
        return result.error || "Could not discard item.";
    }

    await refreshItems();
    return `Discarded ${quantity} ${name}.`;
}

async function handleQuestion(question) {
    const backend = await apiRequest("/api/ask", {
        method: "POST",
        body: JSON.stringify({ question }),
    });

    if (backend.ok) {
        await refreshItems();
        return String(backend.data?.reply || "Done.");
    }

    return handleQuestionLocal(question);
}

async function handleQuestionLocal(question) {
    const q = question.toLowerCase();

    if (/what\s+do\s+i\s+have|list\s+inventory|show\s+inventory/.test(q)) {
        if (!state.items.length) return "Inventory is empty.";
        return state.items
            .map((item) => `${item.name}: ${item.quantity} (${item.usage_unit || "day"})`)
            .join("\n");
    }

    const howManyMatch = q.match(/how\s+many\s+(.+?)\s+(do\s+i\s+have|are\s+left|left)\??$/);
    if (howManyMatch) {
        const rawName = howManyMatch[1].trim();
        const item = findItem(rawName);
        if (!item) return `No '${rawName}' found.`;
        return `${item.name}: ${item.quantity} in stock.`;
    }

    const addMatch = q.match(/add\s+(.+?)\s+quantity\s+(\d+)(?:\s+expiring\s+(\d{4}-\d{2}-\d{2}))?$/);
    if (addMatch) {
        const name = addMatch[1].trim();
        const quantity = Number(addMatch[2]);
        const expiry = addMatch[3] || "";

        const allow = await guardOverpurchase(name, quantity);
        if (!allow) {
            return "Add cancelled to avoid overpurchase.";
        }

        const result = await apiRequest("/api/items", {
            method: "POST",
            body: JSON.stringify({
                name,
                category: "General",
                quantity,
                expiry_date: expiry || "",
                usage_unit: "day",
                usage_history: [],
            }),
        });
        if (!result.ok) {
            return result.error || "Could not add item.";
        }

        await refreshItems();
        return `Added ${quantity} ${name}.`;
    }

    const consumeMatch = q.match(/consume\s+(\d+)\s+(.+)$/);
    if (consumeMatch) {
        const qty = Number(consumeMatch[1]);
        const name = consumeMatch[2].trim();
        return consume(name, qty);
    }

    const throwMatch = q.match(/(?:throw\s+away|discard)\s+(\d+)\s+(.+)$/);
    if (throwMatch) {
        const qty = Number(throwMatch[1]);
        const name = throwMatch[2].trim();
        return throwAway(name, qty);
    }

    return "I can help with: add, consume, throw away, list inventory, and how-many questions.";
}

async function apiRequest(path, options = {}) {
    try {
        const response = await fetch(path, {
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            },
            ...options,
        });

        const text = await response.text();
        const data = text ? JSON.parse(text) : {};
        if (!response.ok) {
            return {
                ok: false,
                error: data.error || `Request failed (${response.status}).`,
                data,
            };
        }
        return { ok: true, data };
    } catch (err) {
        return {
            ok: false,
            error: `Network/API error: ${err.message}`,
            data: null,
        };
    }
}

function formatSustainabilityReport(report) {
    if (!report || typeof report !== "object") {
        return "No sustainability report available.";
    }

    const lines = [];
    const source = String(report.source || "fallback");
    const summary = String(report.summary || "No summary provided.");
    lines.push(`Source: ${source}`);
    lines.push(summary);

    const insights = Array.isArray(report.insights) ? report.insights : [];
    if (!insights.length) {
        lines.push("No item-level insights available.");
        return lines.join("\n\n");
    }

    lines.push("\nRecommendations:");
    for (const item of insights) {
        const name = String(item.name || "Item");
        const issue = String(item.issue || "review");
        lines.push(`- ${name} (${issue})`);

        const rationale = item.rationale;
        if (typeof rationale === "string" && rationale.trim()) {
            lines.push(`  Why: ${rationale.trim()}`);
        } else {
            if (item.estimated_excess_before_expiry !== undefined && item.estimated_excess_before_expiry !== null) {
                lines.push(`  Estimated excess before expiry: ${item.estimated_excess_before_expiry}`);
            }
            if (item.days_until_expiry !== undefined && item.days_until_expiry !== null) {
                lines.push(`  Days until expiry: ${item.days_until_expiry}`);
            }
        }

        const actions = Array.isArray(item.actions)
            ? item.actions
            : Array.isArray(item.suggestions)
                ? item.suggestions
                : [];
        for (const action of actions) {
            lines.push(`  - ${action}`);
        }
    }

    return lines.join("\n");
}
