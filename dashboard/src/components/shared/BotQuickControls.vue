<template>
  <v-card class="quick-controls pa-5" elevation="0" data-testid="bot-quick-controls">
    <div class="d-flex align-center justify-space-between ga-3 mb-4">
      <h2 class="text-h3">{{ tm('quickControls.title') }}</h2>
      <v-btn icon="mdi-refresh" variant="text" size="small" :loading="loading"
        :aria-label="tm('quickControls.refresh')" @click="refreshAll" />
    </div>
    <v-alert v-if="controlsError" type="error" variant="tonal" density="compact" class="mb-3">
      {{ controlsError }}
    </v-alert>
    <v-select v-model="sessionKey" :items="sessions" item-value="session_key" :item-title="sessionTitle"
      class="quick-controls__session" :label="tm('quickControls.session')" density="compact" variant="outlined"
      hide-details :disabled="loading || savingReply" />
    <div class="quick-controls__actions">
      <div class="quick-controls__switches">
        <v-switch :model-value="replyEnabled" :label="tm('quickControls.reply')" color="primary"
          :disabled="loading || savingReply || !selectedSession?.native || !controlsReady" :loading="savingReply"
          hide-details inset data-testid="quick-reply-switch" @update:model-value="setReply" />
        <v-switch :model-value="proactiveEnabled" :label="tm('quickControls.proactive')" color="primary"
          :disabled="loading || savingProactive || !contact" :loading="savingProactive" hide-details inset
          data-testid="quick-proactive-switch" @update:model-value="setProactive" />
      </div>
      <div class="quick-controls__login">
        <v-chip :color="login?.online ? 'success' : 'warning'" size="small" variant="tonal">
          {{ loginLabel }}<span v-if="login?.account" class="ms-2">{{ login.account }}</span>
        </v-chip>
        <v-btn v-if="login?.configured" prepend-icon="mdi-qrcode" variant="tonal" color="primary"
          :disabled="login.online" @click="openLogin">{{ tm('quickControls.qqLogin') }}</v-btn>
      </div>
    </div>
    <v-dialog v-model="loginDialog" max-width="420">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm('quickControls.qqLogin') }}</v-card-title>
        <v-card-text class="text-center">
          <v-chip :color="login?.online ? 'success' : 'warning'" variant="tonal" class="mb-4">{{ loginLabel }}</v-chip>
          <v-alert v-if="loginError" type="error" variant="tonal" density="compact" class="mb-3">{{ loginError }}</v-alert>
          <v-progress-linear v-if="loadingQr" indeterminate color="primary" class="mb-4" />
          <QrCodeViewer v-if="!login?.online && qrPayload" :value="qrPayload" :alt="tm('quickControls.qqLogin')" />
          <v-icon v-if="login?.online" size="72" color="success">mdi-check-circle-outline</v-icon>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-btn v-if="!login?.online" variant="tonal" :loading="loadingQr" @click="getQrCode(true)">
            {{ tm('quickControls.refreshQr') }}
          </v-btn>
          <v-spacer />
          <v-btn variant="text" @click="loginDialog = false">{{ tm('quickControls.close') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { configProfileApi, napcatApi, pluginExtensionApi } from '@/api/v1';
import type { NapCatLoginStatus } from '@/api/v1';
import { useModuleI18n } from '@/i18n/composables';
import QrCodeViewer from './QrCodeViewer.vue';

type Session = { session_key: string; unified_msg_origin: string; display_name?: string;
  participant_id: string; llm_enabled: boolean; session_enabled: boolean; native: boolean; managed?: boolean };
type ContactSection = { edit_token: string; settings: { enabled: boolean; automatic_enabled: boolean } };
type ReplyConfig = { provider_settings: { enable: boolean } } & Record<string, unknown>;
const prefix = 'astrbot_plugin_private_companion';
const { tm } = useModuleI18n('features/welcome');
const sessions = ref<Session[]>([]);
const sessionKey = ref('');
const globalReplyEnabled = ref(false);
const controlsReady = ref(false);
const contact = ref<ContactSection | null>(null);
const loading = ref(false);
const savingReply = ref(false);
const savingProactive = ref(false);
const controlsError = ref('');
const login = ref<NapCatLoginStatus | null>(null);
const loginError = ref('');
const loginDialog = ref(false);
const qrPayload = ref('');
const loadingQr = ref(false);
let stopped = false;
let pollGeneration = 0;
let polling: ReturnType<typeof setTimeout> | undefined;
const selectedSession = computed(() => sessions.value.find(s => s.session_key === sessionKey.value));
const replyEnabled = computed(() => globalReplyEnabled.value && !!selectedSession.value?.llm_enabled
  && !!selectedSession.value?.session_enabled);
const proactiveEnabled = computed(() => !!contact.value?.settings.enabled && !!contact.value?.settings.automatic_enabled);
const loginLabel = computed(() => {
  if (loginError.value) return tm('quickControls.connectionFailed');
  if (!login.value) return tm('quickControls.reading');
  if (!login.value.configured) return tm('quickControls.unconfigured');
  if (login.value.online) return tm('quickControls.online');
  if (['qrcode_scanned', 'initializing'].includes(login.value.phase)) return tm('quickControls.confirming');
  if (login.value.phase === 'reconnecting') return tm('quickControls.reconnecting');
  return tm('quickControls.offline');
});

function sessionTitle(session: Session) {
  return session.display_name ? `${session.display_name} · ${session.participant_id}` : session.participant_id;
}

async function readControls() {
  const results = await Promise.allSettled([
    configProfileApi.get('default'),
    pluginExtensionApi.get(`${prefix}/accounts`),
    pluginExtensionApi.get(`${prefix}/config`, { params: { sections: 'proactive.contact' } }),
  ]);
  controlsReady.value = false;
  contact.value = null;
  controlsError.value = '';
  const [profile, accounts, active] = results;
  if (profile.status === 'fulfilled' && profile.value.data.status === 'ok'
      && accounts.status === 'fulfilled' && accounts.value.data.status === 'ok') {
    globalReplyEnabled.value = (profile.value.data.data.config as ReplyConfig).provider_settings.enable;
    sessions.value = accounts.value.data.data.discovered.targets.filter((s: Session) =>
      s.unified_msg_origin.includes(':FriendMessage:'));
    if (!sessions.value.some(s => s.session_key === sessionKey.value)) {
      sessionKey.value = (sessions.value.find(s => s.managed) || sessions.value[0])?.session_key || '';
    }
    controlsReady.value = true;
  } else {
    controlsError.value = tm('quickControls.readFailed');
  }
  if (active.status === 'fulfilled' && active.value.data.status === 'ok') {
    contact.value = active.value.data.data.sections['proactive.contact'];
  }
}

async function setReply(value: boolean | null) {
  if (!selectedSession.value || savingReply.value) return;
  savingReply.value = true;
  controlsError.value = '';
  try {
    if (value && !globalReplyEnabled.value) {
      const profile = await configProfileApi.get('default');
      if (profile.data.status !== 'ok') throw new Error();
      const config = profile.data.data.config as ReplyConfig;
      config.provider_settings.enable = true;
      const result = await configProfileApi.update('default', config);
      if (result.data.status !== 'ok') throw new Error();
    }
    const response = await pluginExtensionApi.post(`${prefix}/accounts/session`, {
      session_key: sessionKey.value, llm_enabled: !!value, session_enabled: !!value,
    });
    if (response.data.status !== 'ok') throw new Error();
    await readControls();
  } catch {
    await readControls();
    controlsError.value = tm('quickControls.saveFailed');
  } finally { savingReply.value = false; }
}

async function setProactive(value: boolean | null) {
  if (!contact.value || savingProactive.value) return;
  savingProactive.value = true;
  controlsError.value = '';
  try {
    const response = await pluginExtensionApi.post(`${prefix}/config/configure`, {
      section: 'proactive.contact', operation_key: `quick-contact:${crypto.randomUUID()}`,
      edit_token: contact.value.edit_token, changes: { enabled: !!value, automatic_enabled: !!value },
    });
    if (response.data.status !== 'ok') throw new Error();
    await readControls();
  } catch {
    await readControls();
    controlsError.value = tm('quickControls.saveFailed');
  } finally { savingProactive.value = false; }
}

async function readLogin() {
  try {
    const response = await napcatApi.status();
    if (response.data.status !== 'ok') throw new Error();
    const wasOnline = login.value?.online;
    login.value = response.data.data;
    loginError.value = '';
    if (login.value.online) {
      qrPayload.value = '';
      if (!loading.value && (wasOnline === false || !contact.value || !controlsReady.value)) await readControls();
    }
  } catch { loginError.value = tm('quickControls.connectionFailed'); }
}

async function getQrCode(refresh: boolean) {
  if (loadingQr.value) return;
  loadingQr.value = true;
  loginError.value = '';
  try {
    const response = await napcatApi.qrcode(refresh);
    if (response.data.status !== 'ok') throw new Error();
    login.value = response.data.data;
    qrPayload.value = response.data.data.qrcode || '';
  } catch { loginError.value = tm('quickControls.qrFailed'); }
  finally { loadingQr.value = false; }
}

async function openLogin() {
  loginDialog.value = true;
  await getQrCode(false);
}

async function refreshAll() {
  if (loading.value) return;
  loading.value = true;
  try { await Promise.allSettled([readControls(), readLogin()]); }
  finally { loading.value = false; }
}

async function pollLogin(generation: number) {
  if (stopped || generation !== pollGeneration) return;
  if (document.visibilityState !== 'hidden' && !loading.value && !savingReply.value && !savingProactive.value) {
    await readLogin();
    if (loginDialog.value && !login.value?.online && !qrPayload.value
        && login.value?.phase === 'waiting_qrcode') await getQrCode(false);
  }
  if (!stopped && generation === pollGeneration) {
    polling = setTimeout(() => pollLogin(generation), loginDialog.value ? 2000 : 15000);
  }
}

watch(loginDialog, () => {
  clearTimeout(polling);
  const generation = ++pollGeneration;
  if (!loginDialog.value) qrPayload.value = '';
  polling = setTimeout(() => pollLogin(generation), 2000);
});
onMounted(async () => {
  await refreshAll();
  const generation = pollGeneration;
  if (!stopped) polling = setTimeout(() => pollLogin(generation), 15000);
});
onUnmounted(() => { stopped = true; clearTimeout(polling); });
</script>

<style scoped>
.quick-controls { border-radius: 16px; }
.quick-controls__session { max-width: 520px; margin-bottom: 12px; }
.quick-controls__actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 24px; }
.quick-controls__switches { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 32px; min-width: 0; }
.quick-controls__switches :deep(.v-switch) { flex: 0 0 auto; }
.quick-controls__login { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 12px; }
@media (max-width: 600px) {
  .quick-controls__actions { flex-direction: column; align-items: stretch; }
  .quick-controls__switches { column-gap: 16px; }
  .quick-controls__login { justify-content: flex-start; }
}
</style>
