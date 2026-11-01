<template>
  <div class="review-page">
    <div class="d-flex align-center justify-space-between mb-6 ga-3 flex-wrap">
      <h1 class="text-h3">{{ label("title") }}</h1>
      <v-btn
        v-if="state !== 'presets'"
        variant="tonal"
        :disabled="!personaId || busy"
        @click="
          newDialogs = ['', ''];
          newDialog = true;
        "
        >{{ label("create") }}</v-btn
      >
    </div>
    <v-card variant="outlined" class="pa-4 mb-5">
      <div :class="state === 'presets' ? 'preset-controls' : 'review-controls'">
        <v-select
          v-model="personaId"
          :items="personas"
          item-title="persona_id"
          item-value="persona_id"
          :label="label('persona')"
          hide-details
          :disabled="busy"
        />
        <v-select
          v-if="state !== 'presets'"
          v-model="conversationId"
          :items="sources"
          item-title="label"
          item-value="conversation_id"
          :label="label('conversation')"
          hide-details
          :disabled="busy"
        />
        <v-text-field
          v-if="state !== 'presets'"
          v-model.number="contextRounds"
          type="number"
          min="0"
          max="10"
          :label="label('contextRounds')"
          hide-details
          :disabled="busy"
        />
        <v-btn
          v-if="state !== 'presets'"
          variant="tonal"
          :disabled="!personaId || !conversationId || busy"
          @click="importConversation"
          >{{ label("import") }}</v-btn
        >
      </div>
    </v-card>
    <v-tabs v-model="state" class="mb-4" :disabled="busy">
      <v-tab v-for="item in states" :key="item" :value="item">{{
        label(item)
      }}</v-tab>
    </v-tabs>
    <v-progress-linear v-if="loading" indeterminate class="mb-4" />
    <template v-if="state === 'presets'">
      <div v-if="presetBudget" class="mb-4">
        <div class="d-flex justify-space-between flex-wrap ga-2 mb-2">
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
          rounded
          height="6"
        />
        <v-alert
          v-if="presetOverBudget"
          type="error"
          variant="tonal"
          class="mt-3"
        >
          {{ label("overBudget") }}
        </v-alert>
      </div>
      <div class="d-flex ga-2 mb-4">
        <v-btn
          variant="text"
          color="error"
          :disabled="busy || !selectedPresets.length"
          @click="removePresets(selectedPresets)"
          >{{ label("deleteSelected") }}</v-btn
        >
        <v-spacer />
        <v-btn
          variant="tonal"
          :disabled="busy || loading || !personaId || presetOverBudget"
          @click="savePresets"
          >{{ label("save") }}</v-btn
        >
      </div>
      <v-card
        v-for="round in presetPage"
        :key="round"
        variant="outlined"
        class="mb-4 pa-4"
      >
        <div class="d-flex align-center mb-2">
          <v-checkbox
            v-model="selectedPresets"
            :value="round"
            :label="String(round + 1)"
            :aria-label="label('selectRound')"
            hide-details
            density="compact"
            :disabled="busy"
          />
          <v-btn
            variant="text"
            color="error"
            :disabled="busy"
            @click="removePresets([round])"
          >
            {{ label("deleteRound") }}
          </v-btn>
        </div>
        <v-textarea
          v-model="presetDialogs[round * 2]"
          :label="label('user')"
          auto-grow
          rows="2"
          hide-details
          class="mb-2"
          :disabled="busy"
        />
        <v-textarea
          v-model="presetDialogs[round * 2 + 1]"
          :label="label('assistant')"
          auto-grow
          rows="2"
          hide-details
          :disabled="busy"
        />
      </v-card>
    </template>
    <v-card
      v-for="review in state === 'presets' ? [] : reviews"
      :key="review.review_id"
      variant="outlined"
      class="mb-4 pa-4"
    >
      <div
        class="d-flex align-center justify-space-between ga-3 mb-4 flex-wrap"
      >
        <span class="text-subtitle-1">{{
          review.source_title || review.persona_id
        }}</span>
        <v-chip size="small">{{ label(review.state) }}</v-chip>
      </div>
      <v-textarea
        v-for="(_, index) in review.begin_dialogs"
        :key="index"
        v-model="review.begin_dialogs[index]"
        auto-grow
        rows="2"
        class="mb-2"
        :label="label(index % 2 ? 'assistant' : 'user')"
        hide-details
        :readonly="review.state !== 'pending'"
        :disabled="busy"
      />
      <div v-if="review.state === 'pending'" class="d-flex ga-2 mt-4 flex-wrap">
        <v-btn
          variant="text"
          :disabled="busy"
          @click="review.begin_dialogs.push('', '')"
          >{{ label("addRound") }}</v-btn
        >
        <v-btn
          v-if="review.begin_dialogs.length > 2"
          variant="text"
          :disabled="busy"
          @click="review.begin_dialogs.splice(-2)"
          >{{ label("removeRound") }}</v-btn
        >
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="busy || !valid(review.begin_dialogs)"
          @click="decide(review, 'save')"
          >{{ label("save") }}</v-btn
        >
        <v-btn
          variant="text"
          color="error"
          :disabled="busy || !valid(review.begin_dialogs)"
          @click="decide(review, 'reject')"
          >{{ label("reject") }}</v-btn
        >
        <v-btn
          variant="tonal"
          color="primary"
          :disabled="busy || !valid(review.begin_dialogs)"
          @click="decide(review, 'approve')"
          >{{ label("approve") }}</v-btn
        >
      </div>
    </v-card>
    <v-pagination
      v-if="total > 20"
      v-model="page"
      :length="Math.ceil(total / 20)"
      :disabled="busy"
    />
    <v-dialog v-model="newDialog" max-width="800" :persistent="busy">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          label("create")
        }}</v-card-title>
        <v-card-text>
          <v-textarea
            v-for="(_, index) in newDialogs"
            :key="index"
            v-model="newDialogs[index]"
            auto-grow
            rows="3"
            :label="label(index % 2 ? 'assistant' : 'user')"
          />
        </v-card-text>
        <v-card-actions class="pa-4">
          <v-btn
            variant="text"
            :disabled="busy"
            @click="newDialogs.push('', '')"
            >{{ label("addRound") }}</v-btn
          >
          <v-spacer />
          <v-btn variant="text" :disabled="busy" @click="newDialog = false">{{
            label("cancel")
          }}</v-btn>
          <v-btn
            variant="tonal"
            :disabled="busy || !valid(newDialogs)"
            @click="saveNew"
            >{{ label("save") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-snackbar
      v-model="noticeOpen"
      :color="noticeError ? 'error' : 'success'"
      >{{ notice }}</v-snackbar
    >
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "@/i18n/composables";
import { apiV1Client } from "@/api/http";

type ReviewState = "pending" | "approved" | "rejected";
interface Review {
  review_id: string;
  persona_id: string;
  source_title: string;
  begin_dialogs: string[];
  state: ReviewState;
  revision: number;
}
interface Source {
  conversation_id: string;
  title: string | null;
  user_id: string;
  label: string;
}
const { t } = useI18n();
const label = (key: string) => t(`features.persona.review.${key}`);
const endpoint = "/persona-example-reviews";
const states = ["pending", "presets", "approved", "rejected"] as const;
const personas = ref<{ persona_id: string }[]>([]);
const sources = ref<Source[]>([]);
const personaId = ref("");
const conversationId = ref("");
const contextRounds = ref(2);
const reviews = ref<Review[]>([]);
const state = ref<ReviewState | "presets">("pending");
const page = ref(1);
const total = ref(0);
const loading = ref(false);
const busy = ref(false);
const newDialog = ref(false);
const newDialogs = ref(["", ""]);
const presetDialogs = ref<string[]>([]);
const presetRevision = ref("");
const selectedPresets = ref<number[]>([]);
const presetBudget = ref<{
  context_tokens: number;
  limit_percent: number;
  limit_tokens: number;
} | null>(null);
const presetTokens = computed(() =>
  presetDialogs.value.reduce((total, value) => {
    let chinese = 0;
    let emoji = 0;
    const characters = Array.from(value);
    for (const character of characters) {
      if (character >= "\u4e00" && character <= "\u9fff") chinese++;
      else if (
        /[\u200d\u2600-\u27bf\ufe00-\ufe0f\u{1f000}-\u{1faff}]/u.test(character)
      )
        emoji++;
    }
    return (
      total +
      Math.floor(
        chinese * 0.6 + emoji * 2 + (characters.length - chinese - emoji) * 0.3,
      )
    );
  }, 0),
);
const presetPercent = computed(() =>
  presetBudget.value
    ? (presetTokens.value * 100) / presetBudget.value.context_tokens
    : 0,
);
const presetOverBudget = computed(() =>
  Boolean(
    presetBudget.value && presetTokens.value > presetBudget.value.limit_tokens,
  ),
);
const presetPage = computed(() =>
  Array.from(
    { length: presetDialogs.value.length / 2 },
    (_, index) => index,
  ).slice((page.value - 1) * 20, page.value * 20),
);
const notice = ref("");
const noticeOpen = ref(false);
const noticeError = ref(false);
let requestId = 0;
const valid = (dialogs: string[]) =>
  dialogs.length >= 2 && dialogs.every((value) => value.trim());

function showNotice(error?: unknown) {
  noticeError.value = Boolean(error);
  const detail = error as
    | { response?: { data?: { message?: string } }; message?: string }
    | undefined;
  notice.value =
    detail?.response?.data?.message || detail?.message || label("saved");
  noticeOpen.value = true;
}

async function loadReviews() {
  const current = ++requestId;
  loading.value = true;
  try {
    if (state.value === "presets") {
      const response = await apiV1Client.get(`${endpoint}/presets`, {
        params: { persona_id: personaId.value },
      });
      if (current !== requestId) return;
      presetDialogs.value = response.data.data.begin_dialogs;
      presetRevision.value = response.data.data.revision;
      presetBudget.value = response.data.data.budget;
      selectedPresets.value = [];
      total.value = presetDialogs.value.length / 2;
      return;
    }
    const response = await apiV1Client.get(endpoint, {
      params: {
        persona_id: personaId.value,
        state: state.value,
        page: page.value,
      },
    });
    if (current !== requestId) return;
    reviews.value = response.data.data.items;
    total.value = response.data.data.total;
  } catch (error) {
    if (current === requestId) showNotice(error);
  } finally {
    if (current === requestId) loading.value = false;
  }
}

function removePresets(rounds: number[]) {
  const selected = new Set(rounds);
  presetDialogs.value = presetDialogs.value.filter(
    (_, index) => !selected.has(Math.floor(index / 2)),
  );
  selectedPresets.value = [];
  total.value = presetDialogs.value.length / 2;
  page.value = Math.min(page.value, Math.max(1, Math.ceil(total.value / 20)));
}

async function savePresets() {
  busy.value = true;
  try {
    const response = await apiV1Client.put(`${endpoint}/presets`, {
      persona_id: personaId.value,
      revision: presetRevision.value,
      begin_dialogs: presetDialogs.value,
    });
    presetRevision.value = response.data.data.revision;
    presetBudget.value = response.data.data.budget;
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = false;
  }
}

async function importConversation() {
  busy.value = true;
  try {
    await apiV1Client.post(`${endpoint}/import`, {
      persona_id: personaId.value,
      conversation_id: conversationId.value,
      context_rounds: contextRounds.value,
    });
    state.value = "pending";
    page.value = 1;
    await loadReviews();
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = false;
  }
}

async function saveNew() {
  busy.value = true;
  try {
    await apiV1Client.post(endpoint, {
      persona_id: personaId.value,
      begin_dialogs: newDialogs.value,
    });
    newDialog.value = false;
    state.value = "pending";
    page.value = 1;
    await loadReviews();
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = false;
  }
}

async function decide(review: Review, action: "save" | "approve" | "reject") {
  busy.value = true;
  try {
    const response = await apiV1Client.put(`${endpoint}/${review.review_id}`, {
      revision: review.revision,
      begin_dialogs: review.begin_dialogs,
      action,
    });
    if (action === "save") Object.assign(review, response.data.data);
    else await loadReviews();
    showNotice();
  } catch (error) {
    showNotice(error);
  } finally {
    busy.value = false;
  }
}

watch([personaId, state], () => {
  page.value = 1;
  void loadReviews();
});
watch(page, () => {
  if (state.value !== "presets") void loadReviews();
});
onMounted(async () => {
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
    personaId.value =
      personas.value.find((item) => item.persona_id !== "default")
        ?.persona_id ||
      personas.value[0]?.persona_id ||
      "";
    conversationId.value = sources.value[0]?.conversation_id || "";
    if (!personaId.value) await loadReviews();
  } catch (error) {
    showNotice(error);
  }
});
</script>

<style scoped>
.review-page {
  max-width: 1120px;
  margin: 0 auto;
  padding: 24px;
  width: 100%;
}
.review-controls {
  display: grid;
  grid-template-columns: 1fr 1.5fr 130px auto;
  gap: 16px;
  align-items: center;
}
@media (max-width: 800px) {
  .review-page {
    padding: 16px;
  }
  .review-controls {
    grid-template-columns: 1fr;
  }
}
</style>
