import fs from "node:fs/promises";
import path from "node:path";

const dir = "queue";
for (const file of await fs.readdir(dir)) {
  if (!file.endsWith(".json")) continue;
  const filePath = path.join(dir, file);
  const item = JSON.parse(await fs.readFile(filePath, "utf8"));
  const validSlideCount = Array.isArray(item.slides) && item.slides.length >= 1 && item.slides.length <= 5;
  const sourceGrounded = (item.source_photo_used === true && typeof item.source_image_url === "string" && /^https?:\/\//i.test(item.source_image_url))
    || (item.visual_asset_type === "original_graphic" && Array.isArray(item.visual_asset_source_urls) && item.visual_asset_source_urls.length >= 1);
  const verified = Number(item.verification_source_count || 0) >= 2
    && Array.isArray(item.source_urls)
    && item.source_urls.length >= 2;
  const approvedVisual = (item.ai_generated_art === true && item.visual_asset_rights === "owned")
    || (item.visual_asset_type === "current_official_press_photo"
      && item.visual_asset_rights === "press_use"
      && item.photo_recency_checked === true);
  if (item.status === "ready" && (!approvedVisual || !sourceGrounded || !validSlideCount || !verified)) {
    item.status = "paused";
    item.pause_reason = "Blocked: Sports Wire 24/7 requires two-source verification, a current story-relevant visual and a one- to five-slide post";
    await fs.writeFile(filePath, `${JSON.stringify(item, null, 2)}\n`);
    console.log(`Blocked unsafe queue item: ${file}`);
  }
}
