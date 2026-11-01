<template>
  <div class="review-page">
    <div class="d-flex align-center justify-space-between mb-6 ga-3 flex-wrap">
      <h1 class="text-h3">{{ label("title") }}</h1>
      <v-btn
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
      <div class="review-controls">
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
          v-model="conversationId"
          :items="sources"
          item-title="label"
          item-value="conversation_id"
          :label="label('conversation')"
          hide-details
          :disabled="busy"
        />
        <v-text-field
          v-model.number="contextRounds"
          type="number"
          min="0"
          max="10"
          :label="label('contextRounds')"
          hide-details
          :disabled="busy"
        />
        <v-btn
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
    <v-card
      v-for="review in reviews"
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
import { onMounted, ref, watch } from "vue";
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
const states: ReviewState[] = ["pending", "approved", "rejected"];
const personas = ref<{ persona_id: string }[]>([]);
const sources = ref<Source[]>([]);
const personaId = ref("");
const conversationId = ref("");
const contextRounds = ref(2);
const reviews = ref<Review[]>([]);
const state = ref<ReviewState>("pending");
const page = ref(1);
const total = ref(0);
const loading = ref(false);
const busy = ref(false);
const newDialog = ref(false);
const newDialogs = ref(["", ""]);
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
  void loadReviews();
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
