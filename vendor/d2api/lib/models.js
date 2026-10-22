'use strict';

// DeepSeek Web merged Instant / Expert / Vision into one mode (V4.1-Flash,
// 2026-09). Every model id below therefore maps to the same upstream model and
// only differs in the two toggles the web app still exposes:
//   thinking_enabled -> DeepThink reasoning
//   search_enabled   -> web search
// model_type stays "default" unless overridden with DEEPSEEK_MODEL_TYPE.

const REAL_MODEL = 'DeepSeek-V4.1-Flash (chat.deepseek.com)';

const MODEL_IDS = [
  'deepseek-v4.1-flash',
  'deepseek-v4.1-flash-think',
  'deepseek-v4.1-flash-search',
  'deepseek-v4.1-flash-think-search',
  'deepseek-v4.1',
  'deepseek-v4.1-think',
  'deepseek-v4.1-search',
  'deepseek-v4.1-think-search',
  'deepseek-v4-flash',
  'deepseek-v4-flash-think',
  'deepseek-v4-flash-search',
  'deepseek-v4-flash-think-search',
  'deepseek-chat',
  'deepseek-chat-think',
  'deepseek-chat-search',
  'deepseek-chat-think-search',
  'deepseek-default',
  'deepseek-default-search',
  'deepseek-reasoner',
  'deepseek-reasoner-search',
  'deepseek-r1',
  'deepseek-r1-search',
  'deepseek-v3',
  'deepseek-v3-think',
  'deepseek-v3-search',
  'deepseek-v3-think-search',
  'deepseek-v3.1',
  'deepseek-v3.1-think',
  'deepseek-v3.1-search',
  'deepseek-v3.1-think-search',
  'deepseek-v3.2',
  'deepseek-v3.2-think',
  'deepseek-v3.2-search',
  'deepseek-v3.2-think-search',
  'deepseek-instant',
  'deepseek-instant-think',
  'deepseek-instant-search',
  'deepseek-instant-think-search',
  // Legacy names still accepted by the web app.
  'deepseek-expert',
  'deepseek-expert-think',
  'deepseek-expert-search',
  'deepseek-expert-think-search',
  'deepseek-v4-pro',
  'deepseek-v4-pro-think',
  'deepseek-v4-pro-search',
  'deepseek-v4-pro-think-search',
];

function modelConfig(id) {
  const name = String(id || 'deepseek-v4.1-flash').trim().toLowerCase();
  const thinking = /(?:^|[-_])think(?:ing)?(?:$|[-_])/.test(name)
    || name.includes('reasoner')
    || /(?:^|[-_])r1(?:$|[-_])/.test(name);
  const search = /(?:^|[-_])search(?:$|[-_])/.test(name);

  return {
    id: name,
    modelType: process.env.DEEPSEEK_MODEL_TYPE === undefined
      ? 'default'
      : String(process.env.DEEPSEEK_MODEL_TYPE).trim(),
    thinking,
    search,
    realModel: REAL_MODEL,
  };
}

function isKnownModel(id) {
  return MODEL_IDS.includes(String(id || '').trim().toLowerCase());
}

function modelList() {
  const created = 1735689600;
  return MODEL_IDS.map((id) => {
    const cfg = modelConfig(id);
    return {
      id,
      object: 'model',
      created,
      owned_by: 'deepseek-web',
      permission: [],
      x_deepseek: {
        reasoning: cfg.thinking,
        web_search: cfg.search,
        real_model: cfg.realModel,
      },
    };
  });
}

module.exports = { MODEL_IDS, modelConfig, isKnownModel, modelList, REAL_MODEL };
