<template>
  <div ref="pageRoot" class="review-page">
    <div class="d-flex align-center justify-space-between mb-5 ga-3 flex-wrap">
      <div class="d-flex align-center ga-3">
        <v-btn
          variant="text"
          prepend-icon="mdi-arrow-left"
          @click="returnToPersona"
          >{{ label("back") }}</v-btn
        >
        <h1 class="text-h3">{{ label("title") }}</h1>
      </div>
      <v-select
        :model-value="personaId"
        :items="personas"
        item-title="persona_id"
        item-value="persona_id"
        :label="label('persona')"
        class="persona-select"
        hide-details
        :disabled="busy"
        @update:model-value="selectPersona"
      />
    </div>
    <v-tabs v-model="state" class="mb-5" :disabled="busy">
      <v-tab v-for="item in states" :key="item" :value="item">{{
        label(item)
      }}</v-tab>
    </v-tabs>
    <v-progress-linear v-if="loading" indeterminate class="mb-4" />

    <template v-if="state === 'presets'">
      <v-card v-if="presetBudget" variant="outlined" class="pa-4 mb-5">
        <div class="d-flex justify-space-between flex-wrap ga-2 mb-3">
          <span
            >{{ label("capacity") }} {{ presetPercent.toFixed(2) }}% /
            {{ presetBudget.limit_percent }}%</span
          >
          <span
            >{{ label("remaining") }}
            {{
              Math.max(
                0,
                presetBudget.limit_tokens - presetTokens,
              ).toLocaleString()
            }}
            tokens</span
          >
        </div>
        <v-progress-linear
          :model-value="presetPercent"
          :max="presetBudget.limit_percent"
          :color="presetOverBudget ? 'error' : 'primary'"
          :indeterminate="budgetLoading"
          rounded
          height="6"
        />
        <v-alert
          v-if="presetOverBudget"
          type="error"
          variant="tonal"
          class="mt-3"
          >{{ label("overBudget") }}</v-alert
        >
      </v-card>
      <div class="d-flex align-center ga-2 mb-4 flex-wrap">
        <v-chip size="small" variant="tonal"
          >{{ segments.length }} {{ label("segments") }} ·
          {{ presetDialogs.length / 2 }} {{ label("rounds") }}</v-chip
        >
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="busy || loading"
          @click="reloadPresets"
          >{{ label("refresh") }}</v-btn
        >
        <v-btn
          variant="tonal"
          :loading="savingPresets"
          :disabled="
            busy || loading || !personaId || !presetDirty || presetOverBudget
          "
          @click="savePresets"
          >{{ label("save") }}</v-btn
        >
      </div>
      <v-expansion-panels v-model="expandedSegment" variant="accordion">
        <v-expansion-panel
          v-for="(segment, segmentIndex) in segments"
          :key="segment.id"
          :value="segment.id"
          class="mb-3 preset-segment"
          elevation="0"
        >
          <v-expansion-panel-title>
            <div class="d-flex align-center ga-3 flex-wrap">
              <span>{{
                segment.source_title ||
                `${label("segment")} ${segmentIndex + 1}`
              }}</span>
              <v-chip size="small" variant="tonal"
                >{{ segment.pair_count }} {{ label("rounds") }}</v-chip
              >
            </div>
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="d-flex ga-2 align-center mb-4 flex-wrap">
              <v-text-field
                v-model="segment.source_title"
                :label="label('segmentTitle')"
                hide-details
                density="compact"
                class="segment-title"
                :disabled="busy"
              />
              <v-btn
                variant="text"
                color="error"
                :disabled="busy || !selectedPairs.length"
                @click="removePairs(selectedPairs)"
                >{{ label("deleteSelected") }}</v-btn
              >
              <v-btn
                variant="text"
                color="error"
                :disabled="busy"
                @click="removeSegment(segment)"
                >{{ label("deleteSegment") }}</v-btn
              >
            </div>
            <div
              v-for="pair in visiblePairs(segment)"
              :key="`${segment.id}:${pair}`"
              class="preset-pair mb-4"
            >
              <div class="selection-row">
                <v-checkbox
                  :model-value="selectedPairs"
                  :value="pair"
                  :label="`${label('round')} ${pair - segment.start_pair + 1}`"
                  :aria-label="label('selectRound')"
                  hide-details
                  density="compact"
                  class="selection-checkbox"
                  :disabled="busy"
                  @update:model-value="updateSelection($event, pair, 'presets')"
                />
                <v-btn
                  v-if="
                    pairSelectionAnchor !== null && pairSelectionAnchor !== pair
                  "
                  variant="text"
                  size="small"
                  :disabled="busy"
                  @click="selectToHere(pair, 'presets')"
                  >{{ label("selectToHere") }}</v-btn
                >
              </div>
              <v-textarea
                v-model="presetDialogs[pair * 2]"
                :label="label('user')"
                rows="2"
                auto-grow
                hide-details
                class="mb-3"
                :disabled="busy"
              />
              <v-textarea
                v-model="presetDialogs[pair * 2 + 1]"
                :label="label('assistant')"
                rows="2"
                auto-grow
                hide-details
                :disabled="busy"
              />
            </div>
            <v-pagination
              v-if="segment.pair_count > pageSize"
              v-model="pairPage"
              :length="Math.ceil(segment.pair_count / pageSize)"
              :disabled="busy"
            />
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>

    <template v-else-if="state === 'history'">
      <v-card variant="outlined" class="pa-4 mb-5">
        <div class="history-source">
          <v-select
            v-model="conversationId"
            :items="sources"
            item-title="label"
            item-value="conversation_id"
            :label="label('conversation')"
            hide-details
            :disabled="busy || loading"
          />
          <v-btn
            variant="tonal"
            :disabled="!conversationId || busy || loading"
            @click="loadHistory"
            >{{ label("refresh") }}</v-btn
          >
        </div>
        <div class="history-date-filter mt-4">
          <v-text-field
            v-model="dateStart"
            type="date"
            :label="label('dateStart')"
            density="compact"
            hide-details
            clearable
            :disabled="busy"
          />
          <v-text-field
            v-model="dateEnd"
            type="date"
            :label="label('dateEnd')"
            :min="dateStart || undefined"
            :error-messages="dateRangeInvalid ? label('invalidDateRange') : []"
            density="compact"
            hide-details="auto"
            clearable
            :disabled="busy"
          />
          <v-btn
            variant="text"
            :disabled="busy || (!dateStart && !dateEnd)"
            @click="dateStart = dateEnd = null"
            >{{ label("clearDateFilter") }}</v-btn
          >
        </div>
        <div class="history-range mt-4">
          <v-autocomplete
            v-model="rangeStart"
            :items="historyOptions"
            item-title="label"
            item-value="index"
            :label="label('rangeStart')"
            hide-details
            :disabled="busy"
          />
          <v-autocomplete
            v-model="rangeEnd"
            :items="historyOptions"
            item-title="label"
            item-value="index"
            :label="label('rangeEnd')"
            hide-details
            :disabled="busy"
          />
          <v-btn
            variant="text"
            :disabled="busy || rangeStart === null || rangeEnd === null"
            @click="selectRange"
            >{{ label("selectRange") }}</v-btn
          >
        </div>
      </v-card>
      <div class="d-flex align-center ga-2 mb-4 flex-wrap">
        <v-btn
          variant="text"
          :disabled="
            busy || !historyPage.some((message) => !message.in_presets)
          "
          @click="selectHistoryPage"
          >{{ label("selectPage") }}</v-btn
        >
        <v-btn
          variant="text"
          :disabled="busy || !selectedHistory.length"
          @click="clearHistorySelection"
          >{{ label("clearSelection") }}</v-btn
        >
        <span
          >{{ selectedHistory.length }} {{ label("selectedMessages") }} ·
          {{ historyRanges.length }} {{ label("segments") }}</span
        >
        <v-spacer />
        <v-btn
          variant="tonal"
          :disabled="busy || loading || !personaId || !historyRanges.length"
          @click="importSelectedHistory"
          >{{ label("addPresets") }}</v-btn
        >
      </div>
      <v-card
        v-for="message in historyPage"
        :key="message.index"
        variant="outlined"
        class="pa-4 mb-3 history-message"
        :data-message-index="message.index"
        :data-message-date="message.dateKey"
        :class="{ 'history-message-assistant': message.role === 'assistant' }"
      >
        <div class="selection-row">
          <v-checkbox
            :model-value="selectedHistory"
            :value="message.index"
            :label="`${message.index + 1} · ${label(message.role)}`"
            hide-details
            density="compact"
            class="selection-checkbox"
            :disabled="busy || message.in_presets"
            @update:model-value="
              updateSelection($event, message.index, 'history')
            "
          />
          <v-btn
            v-if="
              historySelectionAnchor !== null &&
              historySelectionAnchor !== message.index &&
              !message.in_presets
            "
            variant="text"
            size="small"
            :disabled="busy"
            @click="selectToHere(message.index, 'history')"
            >{{ label("selectToHere") }}</v-btn
          >
        </div>
        <div class="message-meta">
          <time :datetime="message.isoTime">{{ message.timeLabel }}</time>
          <v-chip
            v-if="message.in_presets"
            size="small"
            color="primary"
            variant="tonal"
            >{{ label("inPresets") }}</v-chip
          >
        </div>
        <div class="message-content">{{ message.content }}</div>
      </v-card>
      <v-card
        v-if="!loading && conversationId && !filteredHistoryMessages.length"
        variant="outlined"
        class="pa-4 history-empty"
        >{{ label("noHistoryMatches") }}</v-card
      >
      <v-pagination
        v-if="filteredHistoryMessages.length > historyPageSize"
        v-model="historyPageNumber"
        :length="Math.ceil(filteredHistoryMessages.length / historyPageSize)"
        :disabled="busy"
      />
    </template>

    <v-btn
      v-if="showBackToTop"
      class="back-to-top"
      variant="tonal"
      color="primary"
      prepend-icon="mdi-arrow-up"
      elevation="4"
      @click="scrollToTop"
      >{{ label("backToTop") }}</v-btn
    >
    <v-snackbar
      v-model="noticeOpen"
      :color="noticeError ? 'error' : 'success'"
      >{{ notice }}</v-snackbar
    >
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
  useRoute,
  useRouter,
} from "vue-router";
import { useI18n } from "@/i18n/composables";
import { apiV1Client } from "@/api/http";
import { askForConfirmation, useConfirmDialog } from "@/utils/confirmDialog";
import {
  removePresetPairs,
  selectedHistoryRanges,
} from "@/utils/personaPresets.mjs";

type PageState = "presets" | "history";
interface Segment {
  id: string;
  start_pair: number;
  pair_count: number;
  source_title?: string;
  source_review_id?: string;
  source_conversation_id?: string;
  source_messages?: string[][];
}
interface Source {
  conversation_id: string;
  title: string | null;
  user_id: string;
  label: string;
}
interface HistoryMessage {
  index: number;
  role: "user" | "assistant";
  content: string;
  sent_at?: number | null;
  source_id?: string;
  in_presets?: boolean;
}
interface Budget {
  context_tokens: number;
  limit_percent: number;
  limit_tokens: number;
  estimated_tokens: number;
  overhead_tokens?: number;
}

const { t, locale } = useI18n();
const label = (key: string) => t(`features.persona.review.${key}`);
const endpoint = "/persona-example-reviews";
const router = useRouter();
const route = useRoute();
const routePersonaId = computed(() =>
  typeof route.query.personaId === "string"
    ? route.query.personaId
    : typeof route.params.personaId === "string"
    ? route.params.personaId
    : undefined,
);
const confirmDialog = useConfirmDialog();
const states: PageState[] = ["presets", "history"];
const state = ref<PageState>(
  states.includes(route.query.tab as PageState)
    ? (route.query.tab as PageState)
    : "presets",
);
const personas = ref<{ persona_id: string }[]>([]);
const sources = ref<Source[]>([]);
const personaId = ref("");
const conversationId = ref("");
const loading = ref(false);
const busy = ref(false);
const initialized = ref(false);
const notice = ref("");
const noticeOpen = ref(false);
const noticeError = ref(false);
const pageSize = 20;
let viewRequest = 0;
const pageRoot = ref<HTMLElement>();
const scrollContainer = ref<HTMLElement>();
const showBackToTop = ref(false);

const presetDialogs = ref<string[]>([]);
const segments = ref<Segment[]>([]);
const presetRevision = ref("");
const presetSnapshot = ref("");
const loadedPersona = ref("");
const expandedSegment = ref<string>();
const pairPage = ref(1);
const selectedPairs = ref<number[]>([]);
const pairSelectionAnchor = ref<number | null>(null);
const presetBudget = ref<Budget | null>(null);
const budgetLoading = ref(false);
const savingPresets = ref(false);
let budgetTimer: ReturnType<typeof setTimeout> | undefined;
let budgetRequest = 0;
const presetContent = computed(() =>
  JSON.stringify({
    begin_dialogs: presetDialogs.value,
    segments: segments.value,
  }),
);
const presetDirty = computed(
  () =>
    Boolean(presetSnapshot.value) &&
    presetContent.value !== presetSnapshot.value,
);
const presetTokens = computed(() => presetBudget.value?.estimated_tokens ?? 0);
const presetPercent = computed(() =>
  presetBudget.value?.context_tokens
    ? (presetTokens.value * 100) / presetBudget.value.context_tokens
    : 0,
);
const presetOverBudget = computed(() =>
  Boolean(
    presetBudget.value && presetTokens.value > presetBudget.value.limit_tokens,
  ),
);

const historyMessages = ref<HistoryMessage[]>([]);
const historyRevision = ref("");
const selectedHistory = ref<number[]>([]);
const historySelectionAnchor = ref<number | null>(null);
const historyPageNumber = ref(1);
const historyPageSize = 40;
const rangeStart = ref<number | null>(null);
const rangeEnd = ref<number | null>(null);
const dateStart = ref<string | null>(null);
const dateEnd = ref<string | null>(null);
const dateRangeInvalid = computed(() =>
  Boolean(dateStart.value && dateEnd.value && dateStart.value > dateEnd.value),
);
const datedHistoryMessages = computed(() =>
  historyMessages.value.map((message) => {
    const date =
      typeof message.sent_at === "number" && message.sent_at > 0
        ? new Date(message.sent_at * 1000)
        : null;
    const valid = date !== null && !Number.isNaN(date.getTime());
    return {
      ...message,
      dateKey: valid
        ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(
            2,
            "0",
          )}-${String(date.getDate()).padStart(2, "0")}`
        : "",
      timeLabel: valid
        ? date.toLocaleString(locale.value, { hour12: false })
        : label("unknownTime"),
      isoTime: valid ? date.toISOString() : undefined,
    };
  }),
);
const filteredHistoryMessages = computed(() => {
  const start = dateStart.value || "",
    end = dateEnd.value || "";
  if (dateRangeInvalid.value) return [];
  if (!start && !end) return datedHistoryMessages.value;
  return datedHistoryMessages.value.filter(
    (message) =>
      message.dateKey &&
      (!start || message.dateKey >= start) &&
      (!end || message.dateKey <= end),
  );
});
const historyPage = computed(() =>
  filteredHistoryMessages.value.slice(
    (historyPageNumber.value - 1) * historyPageSize,
    historyPageNumber.value * historyPageSize,
  ),
);
const historyOptions = computed(() =>
  filteredHistoryMessages.value
    .filter((message) => !message.in_presets)
    .map((message) => ({
      index: message.index,
      label: `${message.index + 1} · ${message.timeLabel} · ${label(
        message.role,
      )} · ${message.content.slice(0, 48)}`,
    })),
);
const historyRanges = computed(() =>
  selectedHistoryRanges(historyMessages.value, selectedHistory.value),
);
let historyRequest = 0;

function showNotice(error?: unknown) {
  noticeError.value = Boolean(error);
  const detail = error as
    | {
        response?: { data?: { message?: string; detail?: string } };
        message?: string;
      }
    | undefined;
  notice.value =
    detail?.response?.data?.message ||
    detail?.response?.data?.detail ||
    detail?.message ||
    label("saved");
  noticeOpen.value = true;
}

function visiblePairs(segment: Segment) {
  return Array.from(
    {
      length: Math.min(
        pageSize,
        Math.max(0, segment.pair_count - (pairPage.value - 1) * pageSize),
      ),
    },
    (_, offset) =>
      segment.start_pair + (pairPage.value - 1) * pageSize + offset,
  );
}
function updateSelection(
  values: number[] | null,
  index: number,
  scope: "presets" | "history",
) {
  const selected = scope === "presets" ? selectedPairs : selectedHistory;
  const anchor =
    scope === "presets" ? pairSelectionAnchor : historySelectionAnchor;
  const next = values ?? [];
  selected.value = next;
  if (next.includes(index)) anchor.value = index;
  else if (anchor.value === index) anchor.value = next[next.length - 1] ?? null;
}
function selectToHere(index: number, scope: "presets" | "history") {
  const selected = scope === "presets" ? selectedPairs : selectedHistory;
  const anchor =
    scope === "presets" ? pairSelectionAnchor : historySelectionAnchor;
  if (anchor.value === null || !selected.value.includes(anchor.value)) return;
  const start = Math.min(anchor.value, index);
  const end = Math.max(anchor.value, index);
  const indexes =
    scope === "presets"
      ? Array.from({ length: end - start + 1 }, (_, offset) => start + offset)
      : filteredHistoryMessages.value
          .filter(
            (message) =>
              !message.in_presets &&
              message.index >= start &&
              message.index <= end,
          )
          .map((message) => message.index);
  selected.value = [...new Set([...selected.value, ...indexes])];
  if (scope === "history") {
    rangeStart.value = anchor.value;
    rangeEnd.value = index;
  }
}
function clearHistorySelection() {
  selectedHistory.value = [];
  historySelectionAnchor.value = null;
}
function updateScrollPosition() {
  showBackToTop.value = (scrollContainer.value?.scrollTop ?? 0) > 320;
}
function scrollToTop() {
  scrollContainer.value?.scrollTo({
    top: 0,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
}
function acceptPresets(data: {
  begin_dialogs: string[];
  segments: Segment[];
  revision: string;
  budget: Budget;
}) {
  budgetRequest++;
  clearTimeout(budgetTimer);
  presetDialogs.value = data.begin_dialogs;
  segments.value = data.segments;
  presetRevision.value = data.revision;
  presetBudget.value = data.budget;
  selectedPairs.value = [];
  pairSelectionAnchor.value = null;
  presetSnapshot.value = presetContent.value;
  loadedPersona.value = personaId.value;
  if (!segments.value.some((segment) => segment.id === expandedSegment.value))
    expandedSegment.value = segments.value[0]?.id;
  pairPage.value = 1;
  budgetLoading.value = false;
}
async function loadPresets() {
  const request = ++viewRequest;
  loading.value = true;
  try {
    const response = await apiV1Client.get(`${endpoint}/presets`, {
      params: { persona_id: personaId.value },
    });
    if (request === viewRequest) acceptPresets(response.data.data);
  } catch (error) {
    if (request === viewRequest) showNotice(error);
  } finally {
    if (request === viewRequest) loading.value = false;
  }
}
async function confirmDiscard() {
  return (
    !presetDirty.value ||
    (await askForConfirmation(label("unsaved"), confirmDialog))
  );
}
async function reloadPresets() {
  if (await confirmDiscard()) await loadPresets();
}
function removePairs(pairs: number[]) {
  const updated = removePresetPairs(presetDialogs.value, segments.value, pairs);
  presetDialogs.value = updated.begin_dialogs;
  segments.value = updated.segments;
  selectedPairs.value = [];
  pairSelectionAnchor.value = null;
  const segment = segments.value.find(
    (item) => item.id === expandedSegment.value,
  );
  if (!segment) expandedSegment.value = segments.value[0]?.id;
  pairPage.value = Math.min(
    pairPage.value,
    Math.max(1, Math.ceil((segment?.pair_count || 0) / pageSize)),
  );
}
function removeSegment(segment: Segment) {
  removePairs(
    Array.from(
      { length: segment.pair_count },
      (_, index) => segment.start_pair + index,
    ),
  );
}
async function savePresets() {
  busy.value = savingPresets.value = true;
  try {
    const response = await apiV1Client.put(`${endpoint}/presets`, {
      persona_id: personaId.value,
      revision: presetRevision.value,
      begin_dialogs: presetDialogs.value,
      segments: segments.value,
    });
    acceptPresets(response.data.data);
    window.dispatchEvent(new CustomEvent("astrbot:persona-saved"));
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = savingPresets.value = false;
  }
}
async function refreshBudget() {
  const request = ++budgetRequest;
  budgetLoading.value = true;
  try {
    const response = await apiV1Client.post(`${endpoint}/presets/budget`, {
      persona_id: personaId.value,
      begin_dialogs: presetDialogs.value,
      segments: segments.value,
    });
    if (request === budgetRequest) presetBudget.value = response.data.data;
  } catch (error) {
    if (request === budgetRequest) showNotice(error);
  } finally {
    if (request === budgetRequest) budgetLoading.value = false;
  }
}
async function loadHistory() {
  const request = ++historyRequest;
  clearHistorySelection();
  historyMessages.value = [];
  historyRevision.value = "";
  historyPageNumber.value = 1;
  rangeStart.value = rangeEnd.value = null;
  if (!conversationId.value) return;
  loading.value = true;
  try {
    const response = await apiV1Client.get(`${endpoint}/history`, {
      params: {
        conversation_id: conversationId.value,
        persona_id: personaId.value,
      },
    });
    if (request !== historyRequest) return;
    historyMessages.value = response.data.data.messages;
    historyRevision.value = response.data.data.revision;
  } catch (error) {
    if (request === historyRequest) showNotice(error);
  } finally {
    if (request === historyRequest) loading.value = false;
  }
}
function selectRange() {
  if (rangeStart.value === null || rangeEnd.value === null) return;
  historySelectionAnchor.value = rangeStart.value;
  const start = Math.min(rangeStart.value, rangeEnd.value);
  const end = Math.max(rangeStart.value, rangeEnd.value);
  selectedHistory.value = [
    ...new Set([
      ...selectedHistory.value,
      ...filteredHistoryMessages.value
        .filter(
          (message) =>
            !message.in_presets &&
            message.index >= start &&
            message.index <= end,
        )
        .map((message) => message.index),
    ]),
  ];
}
function selectHistoryPage() {
  selectedHistory.value = [
    ...new Set([
      ...selectedHistory.value,
      ...historyPage.value
        .filter((message) => !message.in_presets)
        .map((message) => message.index),
    ]),
  ];
  historySelectionAnchor.value ??=
    historyPage.value.find((message) => !message.in_presets)?.index ?? null;
}
async function importSelectedHistory() {
  if (presetDirty.value) {
    showNotice(new Error(label("savePresetsFirst")));
    return;
  }
  const messages = new Map(
    historyMessages.value.map((message) => [message.index, message]),
  );
  if (
    historyRanges.value.some(
      (range: { end: number }) => messages.get(range.end)?.role !== "assistant",
    )
  ) {
    showNotice(new Error(label("incompleteRange")));
    return;
  }
  busy.value = true;
  try {
    await apiV1Client.post(`${endpoint}/import`, {
      persona_id: personaId.value,
      conversation_id: conversationId.value,
      revision: historyRevision.value,
      ranges: historyRanges.value,
      append: true,
    });
    clearHistorySelection();
    loadedPersona.value = "";
    await loadHistory();
    window.dispatchEvent(new CustomEvent("astrbot:persona-saved"));
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = false;
  }
}
async function loadCurrent() {
  if (!initialized.value || !personaId.value) return;
  viewRequest++;
  historyRequest++;
  if (state.value === "presets") {
    if (loadedPersona.value !== personaId.value) await loadPresets();
    else loading.value = false;
  } else if (state.value === "history") await loadHistory();
}
async function selectPersona(id: string) {
  await router.push({
    name: "PersonaExampleReview",
    query: { personaId: id, tab: state.value },
  });
}
async function returnToPersona() {
  await router.push({ name: "Persona", query: { edit: personaId.value } });
}

watch(presetContent, () => {
  clearTimeout(budgetTimer);
  if (presetDirty.value && initialized.value)
    budgetTimer = setTimeout(refreshBudget, 400);
});
watch(expandedSegment, () => {
  pairPage.value = 1;
  selectedPairs.value = [];
  pairSelectionAnchor.value = null;
});
watch([dateStart, dateEnd], () => {
  clearHistorySelection();
  rangeStart.value = rangeEnd.value = null;
  historyPageNumber.value = 1;
});
watch(conversationId, () => {
  if (initialized.value && state.value === "history") void loadHistory();
});
watch(state, () => {
  if (!initialized.value) return;
  void router.replace({ query: { ...route.query, tab: state.value } });
  void loadCurrent();
});
watch(routePersonaId, async (value) => {
  if (
    !initialized.value ||
    typeof value !== "string" ||
    value === personaId.value
  )
    return;
  personaId.value = value;
  budgetRequest++;
  clearTimeout(budgetTimer);
  presetSnapshot.value = "";
  loadedPersona.value = "";
  presetDialogs.value = [];
  segments.value = [];
  presetBudget.value = null;
  await loadCurrent();
});
onBeforeRouteLeave(async () => !busy.value && (await confirmDiscard()));
onBeforeRouteUpdate(
  async (to, from) =>
    to.query.personaId === from.query.personaId ||
    (!busy.value && (await confirmDiscard())),
);
onBeforeUnmount(() => {
  scrollContainer.value?.removeEventListener("scroll", updateScrollPosition);
  clearTimeout(budgetTimer);
  budgetRequest++;
  viewRequest++;
  historyRequest++;
});
onMounted(async () => {
  scrollContainer.value =
    pageRoot.value?.closest<HTMLElement>(".page-content") ?? undefined;
  scrollContainer.value?.addEventListener("scroll", updateScrollPosition, {
    passive: true,
  });
  updateScrollPosition();
  loading.value = true;
  try {
    const [personaResponse, sourceResponse] = await Promise.all([
      apiV1Client.get("/personas"),
      apiV1Client.get(`${endpoint}/sources`),
    ]);
    personas.value = personaResponse.data.data;
    sources.value = sourceResponse.data.data.map((source: Source) => ({
      ...source,
      label: source.title
        ? `${source.title} · ${source.user_id}`
        : source.user_id,
    }));
    const selected = routePersonaId.value
      ? routePersonaId.value
      : personas.value.find((persona) => persona.persona_id !== "default")
          ?.persona_id ||
        personas.value[0]?.persona_id ||
        "";
    if (!personas.value.some((persona) => persona.persona_id === selected))
      throw new Error(label("personaMissing"));
    personaId.value = selected;
    conversationId.value = sources.value[0]?.conversation_id || "";
    if (
      !route.query.personaId ||
      !states.includes(route.query.tab as PageState)
    )
      await router.replace({
        name: "PersonaExampleReview",
        query: { personaId: selected, tab: state.value },
      });
    initialized.value = true;
    await loadCurrent();
  } catch (error) {
    showNotice(error);
    loading.value = false;
  }
});
</script>

<style scoped>
.review-page {
  max-width: 1120px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}
.persona-select {
  flex: 0 1 280px;
  min-width: 200px;
}
.history-source {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
}
.history-date-filter,
.history-range {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
}
.history-date-filter {
  align-items: start;
}
.message-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-left: 40px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: 0.8rem;
}
.preset-segment {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px !important;
  overflow: hidden;
}
.segment-title {
  flex: 1 1 200px;
}
.preset-pair {
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.selection-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.selection-checkbox {
  flex: 1 1 0;
  min-width: 0;
}
.selection-row > .v-btn {
  flex: 0 0 auto;
}
.back-to-top {
  position: fixed;
  right: max(24px, env(safe-area-inset-right));
  bottom: max(24px, env(safe-area-inset-bottom));
  z-index: 5;
}
.message-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.65;
  padding: 6px 0 0 40px;
}
.history-message-assistant {
  background: rgba(var(--v-theme-primary), 0.035);
}
@media (max-width: 700px) {
  .review-page {
    padding: 16px;
  }
  .persona-select {
    flex: 1 1 100%;
  }
  .history-date-filter,
  .history-range {
    grid-template-columns: minmax(0, 1fr);
  }
  .message-content,
  .message-meta {
    padding-left: 0;
  }
}
</style>
