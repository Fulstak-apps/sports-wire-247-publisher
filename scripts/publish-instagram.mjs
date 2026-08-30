import fs from "node:fs/promises";
import path from "node:path";

const instagramToken = process.env.INSTAGRAM_ACCESS_TOKEN;
const instagramUserId = process.env.INSTAGRAM_USER_ID;
const threadsToken = process.env.THREADS_ACCESS_TOKEN;
const threadsUserId = process.env.THREADS_USER_ID;
const publishInstagramStories = process.env.PUBLISH_INSTAGRAM_STORIES === "true";
const repository = process.env.GITHUB_REPOSITORY;
const refName = process.env.GITHUB_REF_NAME || "main";

if (!instagramToken || !instagramUserId || !repository) {
  throw new Error("Missing INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID, or GITHUB_REPOSITORY");
}

const instagramBase = "https://graph.instagram.com";
const threadsBase = "https://graph.threads.net/v1.0";
const queueDir = "queue";
const files = (await fs.readdir(queueDir)).filter((name) => name.endsWith(".json")).sort();
const maxFeedPostsPerRun = Number(process.env.MAX_FEED_POSTS_PER_RUN || 1);
const maxFeedPostsPerRollingDay = 96;
let feedPostsPublishedThisRun = 0;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const mediaUrl = (relativePath) => `https://raw.githubusercontent.com/${repository}/${refName}/${relativePath}`;

function slideUrl(item, index) {
  const remote = Array.isArray(item.media_urls) ? item.media_urls[index] : "";
  return remote && /^https?:\/\//i.test(remote) ? remote : mediaUrl(item.slides[index]);
}

function storyUrl(item) {
  const remote = item.story_media_url || "";
  return remote && /^https?:\/\//i.test(remote) ? remote : mediaUrl(item.story);
}

function hasPublishableVisual(item) {
  if (["original_graphic", "ai_original_editorial_art_from_event_reference"].includes(item.visual_asset_type)
      && item.visual_asset_rights === "owned"
      && item.source_photo_used === true
      && /^https?:\/\//i.test(item.source_image_url || "")
      && Number(item.verification_source_count || 0) >= 2) return true;
  if (item.photo_recency_checked !== true) return false;
  if (!["event_specific", "same_campaign", "current_subject_portrait"].includes(item.photo_event_relevance)) return false;
  if (!item.photo_context_summary || typeof item.photo_context_summary !== "string") return false;
  const capturedAt = Date.parse(`${item.photo_capture_date}T00:00:00Z`);
  return Number.isFinite(capturedAt) && capturedAt <= Date.now();
}

async function save(itemPath, item) {
  await fs.writeFile(itemPath, `${JSON.stringify(item, null, 2)}\n`);
}

async function instagramPost(endpoint, fields) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const body = new URLSearchParams({ ...fields, access_token: instagramToken });
    const response = await fetch(`${instagramBase}/${instagramUserId}/${endpoint}`, { method: "POST", body });
    const payload = await response.json();
    if (response.ok && !payload.error) return payload;
    const retryable = payload.error?.code === 1 && attempt < 2;
    if (!retryable) throw new Error(`${endpoint} failed: ${JSON.stringify(payload)}`);
    await sleep((attempt + 1) * 15_000);
  }
}

async function waitForInstagramContainer(containerId) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const url = new URL(`${instagramBase}/${containerId}`);
    url.searchParams.set("fields", "status_code,status");
    url.searchParams.set("access_token", instagramToken);
    const payload = await (await fetch(url)).json();
    if (payload.status_code === "FINISHED") return;
    if (["ERROR", "EXPIRED"].includes(payload.status_code)) {
      throw new Error(`Instagram container ${containerId} failed: ${JSON.stringify(payload)}`);
    }
    await sleep(10_000);
  }
  throw new Error(`Instagram container ${containerId} did not finish in time`);
}

async function threadsPost(endpoint, fields) {
  if (!threadsToken || !threadsUserId) throw new Error("Missing THREADS_ACCESS_TOKEN or THREADS_USER_ID");
  const body = new URLSearchParams({ ...fields, access_token: threadsToken });
  const response = await fetch(`${threadsBase}/${threadsUserId}/${endpoint}`, { method: "POST", body });
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(`Threads ${endpoint} failed: ${JSON.stringify(payload)}`);
  return payload;
}

async function waitForThreadsContainer(containerId) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const url = new URL(`${threadsBase}/${containerId}`);
    url.searchParams.set("fields", "status,error_message");
    url.searchParams.set("access_token", threadsToken);
    const payload = await (await fetch(url)).json();
    if (payload.status === "FINISHED") return;
    if (["ERROR", "EXPIRED"].includes(payload.status)) {
      throw new Error(`Threads container ${containerId} failed: ${JSON.stringify(payload)}`);
    }
    await sleep(10_000);
  }
  throw new Error(`Threads container ${containerId} did not finish in time`);
}

async function publishInstagramFeed(item) {
  if (item.slides.length === 1) {
    const single = await instagramPost("media", { image_url: slideUrl(item, 0), caption: item.caption });
    await waitForInstagramContainer(single.id);
    return instagramPost("media_publish", { creation_id: single.id });
  }
  const childIds = [];
  for (let index = 0; index < item.slides.length; index += 1) {
    const child = await instagramPost("media", { image_url: slideUrl(item, index), is_carousel_item: "true" });
    await waitForInstagramContainer(child.id);
    childIds.push(child.id);
  }
  const carousel = await instagramPost("media", {
    media_type: "CAROUSEL",
    children: childIds.join(","),
    caption: item.caption
  });
  await waitForInstagramContainer(carousel.id);
  return instagramPost("media_publish", { creation_id: carousel.id });
}

async function publishInstagramStory(item) {
  // Stories use the dedicated 1080x1920 asset. Never publish a 4:5 feed slide as a Story.
  if (!item.story) throw new Error("Story asset missing");
  const story = await instagramPost("media", { media_type: "STORIES", image_url: storyUrl(item) });
  await waitForInstagramContainer(story.id);
  return instagramPost("media_publish", { creation_id: story.id });
}

async function publishThreadsCarousel(item) {
  if (item.slides.length === 1) {
    const single = await threadsPost("threads", {
      media_type: "IMAGE",
      image_url: slideUrl(item, 0),
      text: item.threads_text || item.caption
    });
    await waitForThreadsContainer(single.id);
    return threadsPost("threads_publish", { creation_id: single.id });
  }
  const children = [];
  for (let index = 0; index < item.slides.length; index += 1) {
    const child = await threadsPost("threads", {
      media_type: "IMAGE",
      image_url: slideUrl(item, index),
      is_carousel_item: "true"
    });
    await waitForThreadsContainer(child.id);
    children.push(child.id);
  }
  const carousel = await threadsPost("threads", {
    media_type: "CAROUSEL",
    children: children.join(","),
    text: item.threads_text || item.caption
  });
  await waitForThreadsContainer(carousel.id);
  return threadsPost("threads_publish", { creation_id: carousel.id });
}

const rollingDayStart = Date.now() - 24 * 60 * 60 * 1000;
let feedPostsPublishedInRollingDay = 0;
for (const file of files) {
  const item = JSON.parse(await fs.readFile(path.join(queueDir, file), "utf8"));
  if (item.status === "published" && item.instagram_media_id && item.published_at && Date.parse(item.published_at) >= rollingDayStart) {
    feedPostsPublishedInRollingDay += 1;
  }
}

for (const file of files) {
  const itemPath = path.join(queueDir, file);
  const item = JSON.parse(await fs.readFile(itemPath, "utf8"));
  if (!Array.isArray(item.slides) || item.slides.length < 1 || item.slides.length > 3) {
    console.error(`Skipped ${file}: Sports Wire 24/7 feed posts require one to three slides`);
    continue;
  }
  if (item.status === "paused" || item.status === "media_refresh_required") continue;
  if (item.publish_after && Date.parse(item.publish_after) > Date.now()) continue;

  if (item.status === "ready") {
    if (!Array.isArray(item.source_urls) || item.source_urls.length < 2 || Number(item.verification_source_count || 0) < 2) {
      console.error(`Skipped ${file}: at least two verification sources are required`);
      continue;
    }
    if (!hasPublishableVisual(item)) {
      console.error(`Skipped ${file}: current/relevant visual verification is missing`);
      continue;
    }
    if (feedPostsPublishedThisRun >= maxFeedPostsPerRun) continue;
    if (feedPostsPublishedInRollingDay >= maxFeedPostsPerRollingDay) continue;
    const published = await publishInstagramFeed(item);
    item.status = "published";
    item.instagram_media_id = published.id;
    item.published_at = new Date().toISOString();
    await save(itemPath, item);
    feedPostsPublishedThisRun += 1;
    feedPostsPublishedInRollingDay += 1;
    console.log(`Published Instagram feed ${file}: ${published.id}`);
  }

  if (item.status !== "published") continue;

  if (publishInstagramStories && item.story && !item.instagram_story_status) {
    try {
      const published = await publishInstagramStory(item);
      item.instagram_story_status = "published";
      item.instagram_story_media_id = published.id;
      item.instagram_story_published_at = new Date().toISOString();
      console.log(`Published Instagram Story ${file}: ${published.id}`);
    } catch (error) {
      item.instagram_story_status = "failed";
      item.instagram_story_error = error.message;
      console.error(`Instagram Story failed for ${file}: ${error.message}`);
    }
    await save(itemPath, item);
  }

  // Threads is required for every published Sports Wire 24/7 carousel. Honor queue items that
  // were previously marked Instagram-only so they can be backfilled automatically.
  if (threadsToken && threadsUserId && (!item.threads_status || item.threads_status === "skipped_for_instagram_only_post")) {
    try {
      const published = await publishThreadsCarousel(item);
      item.threads_status = "published";
      item.threads_media_id = published.id;
      item.threads_published_at = new Date().toISOString();
      console.log(`Published Threads carousel ${file}: ${published.id}`);
    } catch (error) {
      item.threads_status = "failed";
      item.threads_error = error.message;
      console.error(`Threads failed for ${file}: ${error.message}`);
    }
    await save(itemPath, item);
  }
}
