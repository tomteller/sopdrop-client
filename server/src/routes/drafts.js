/**
 * Draft Asset Routes
 *
 * Handles the hybrid publish workflow:
 * 1. Houdini uploads package data as a draft
 * 2. User completes listing in browser (title, description, thumbnail, etc.)
 * 3. User publishes the draft to make it live
 */

import { Router } from 'express';
import multer from 'multer';
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import { query, getClient } from '../models/db.js';
import { authenticate, requireScope } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { validateChopPackage, extractAssetMetadata } from '../services/validation.js';
import { uploadLimiter, sanitizePlainText, sanitizeMarkdown, sanitizeTags } from '../middleware/security.js';
import storage from '../services/storage.js';
import { validateUploads } from '../services/fileValidation.js';

const router = Router();

// Draft expiration time (24 hours)
const DRAFT_EXPIRATION_MS = 24 * 60 * 60 * 1000;

/**
 * Safely parse JSON with size limit to prevent DoS
 */
function safeJSONParse(content, maxSize = 10 * 1024 * 1024) {
  if (!content || content.length > maxSize) {
    throw new ValidationError(`Content too large (max ${Math.round(maxSize / 1024 / 1024)}MB)`);
  }

  try {
    return JSON.parse(content);
  } catch (e) {
    throw new ValidationError('Invalid JSON content');
  }
}

// Thumbnail upload configuration (memory storage)
const uploadThumbnail = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 10 * 1024 * 1024, // 10MB max for thumbnails
  },
  fileFilter: (req, file, cb) => {
    const allowedMimes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (allowedMimes.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new ValidationError('Only images (JPEG, PNG, GIF, WebP) are allowed for thumbnails'));
    }
  },
});

// Media upload configuration (memory storage, images and videos)
const uploadMedia = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 50 * 1024 * 1024, // 50MB max for media
  },
  fileFilter: (req, file, cb) => {
    const allowedMimes = [
      'image/jpeg', 'image/png', 'image/gif', 'image/webp',
      'video/mp4', 'video/webm', 'video/quicktime'
    ];
    if (allowedMimes.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new ValidationError('Only images (JPEG, PNG, GIF, WebP) and videos (MP4, WebM, MOV) are allowed'));
    }
  },
});

/**
 * Helper: Save .sopdrop package to storage
 */
async function saveDraftPackage(packageData, draftId) {
  const content = JSON.stringify(packageData, null, 2);
  const key = `drafts/${draftId}.sopdrop`;

  await storage.upload(key, content, 'application/json');

  return {
    filePath: storage.keyToPath(key),
    fileSize: Buffer.byteLength(content, 'utf-8'),
  };
}

/**
 * Helper: Load draft package from storage
 */
async function loadDraftPackage(draftId) {
  const key = `drafts/${draftId}.sopdrop`;

  if (!(await storage.exists(key))) {
    return null;
  }

  const buf = await storage.download(key);
  return safeJSONParse(buf.toString('utf-8'));
}

/**
 * Helper: Delete draft package from storage
 */
async function deleteDraftPackage(draftId) {
  await storage.remove(`drafts/${draftId}.sopdrop`);
}

/**
 * Helper: Save base64-encoded screenshot as thumbnail
 */
async function saveScreenshotFromBase64(base64Data, draftId) {
  try {
    // Validate base64 data
    if (!base64Data || typeof base64Data !== 'string') {
      return null;
    }

    // Remove data URL prefix if present (e.g., "data:image/png;base64,")
    let cleanBase64 = base64Data;
    if (base64Data.includes(',')) {
      cleanBase64 = base64Data.split(',')[1];
    }

    // Decode base64 to buffer
    const imageBuffer = Buffer.from(cleanBase64, 'base64');

    // Validate it's actually image data (check PNG/JPEG magic bytes)
    const isPng = imageBuffer[0] === 0x89 && imageBuffer[1] === 0x50;
    const isJpeg = imageBuffer[0] === 0xFF && imageBuffer[1] === 0xD8;

    if (!isPng && !isJpeg) {
      console.warn('Screenshot does not appear to be a valid PNG or JPEG');
      return null;
    }

    // Save with appropriate extension
    const ext = isPng ? '.png' : '.jpg';
    const filename = `${draftId}${ext}`;
    const key = `thumbnails/${filename}`;
    const contentType = isPng ? 'image/png' : 'image/jpeg';

    await storage.upload(key, imageBuffer, contentType);

    console.log(`Saved screenshot: ${key} (${imageBuffer.length} bytes)`);

    return storage.keyToPath(key);
  } catch (error) {
    console.error('Failed to save screenshot:', error);
    return null;
  }
}

/**
 * Helper: Create URL-safe slug
 */
function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .substring(0, 100);
}

/**
 * Helper: Calculate hash from string content
 */
function calculateHashFromString(content) {
  return crypto.createHash('sha256').update(content).digest('hex');
}

/**
 * POST /drafts
 * Create a new draft from Houdini (upload package data only)
 *
 * This is called by the Houdini shelf tool to upload the node package.
 * Returns a draft ID that the user will use to complete the listing in browser.
 */
router.post('/', authenticate, requireScope('write'), uploadLimiter, async (req, res, next) => {
  try {
    const { package: chopPackage, screenshot, prefill, additional_images } = req.body;

    if (!chopPackage) {
      throw new ValidationError('Package data is required');
    }

    // Parse package if it's a string
    let packageData;
    try {
      packageData = typeof chopPackage === 'string' ? JSON.parse(chopPackage) : chopPackage;
    } catch (e) {
      throw new ValidationError('Invalid package JSON');
    }

    // Validate the .sopdrop package
    validateChopPackage(packageData);

    // Extract metadata for preview
    const assetMeta = extractAssetMetadata(packageData);

    // Generate draft ID
    const draftId = uuidv4();

    // Save package to storage
    const { filePath, fileSize } = await saveDraftPackage(packageData, draftId);

    // Save screenshot if provided
    let thumbnailUrl = null;
    if (screenshot) {
      thumbnailUrl = await saveScreenshotFromBase64(screenshot, draftId);
      if (thumbnailUrl) {
        console.log(`Screenshot saved for draft ${draftId}: ${thumbnailUrl}`);
      }
    }

    // Save additional images as media
    let mediaArray = [];
    if (additional_images && Array.isArray(additional_images)) {
      for (let i = 0; i < additional_images.length; i++) {
        const imgData = additional_images[i];
        const imgUrl = await saveScreenshotFromBase64(imgData, `${draftId}-media-${i}`);
        if (imgUrl) {
          mediaArray.push({
            url: imgUrl,
            type: 'image',
            caption: null,
          });
        }
      }
      if (mediaArray.length > 0) {
        console.log(`Saved ${mediaArray.length} additional images for draft ${draftId}`);
      }
    }

    // Extract prefill data (name, description, tags from Houdini client)
    const prefillName = prefill?.name ? sanitizePlainText(prefill.name, 100) : null;
    const prefillDescription = prefill?.description ? sanitizeMarkdown(prefill.description, 2000) : null;
    const prefillTags = prefill?.tags ? sanitizeTags(prefill.tags) : [];

    // Store draft metadata in database
    const expiresAt = new Date(Date.now() + DRAFT_EXPIRATION_MS);

    await query(`
      INSERT INTO asset_drafts (
        draft_id, owner_id, file_path, file_size,
        houdini_context, node_count, node_names, node_types,
        houdini_version, has_hda_dependencies, dependencies,
        expires_at, thumbnail_url, name, description, tags, media
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
    `, [
      draftId,
      req.user.id,
      filePath,
      fileSize,
      assetMeta.context,
      assetMeta.nodeCount,
      assetMeta.nodeNames,
      assetMeta.nodeTypes,
      assetMeta.houdiniVersion,
      assetMeta.hasHdaDependencies,
      JSON.stringify(assetMeta.dependencies || []),
      expiresAt,
      thumbnailUrl,
      prefillName,
      prefillDescription,
      prefillTags,
      JSON.stringify(mediaArray),
    ]);

    // Return draft ID and URL for browser completion
    const webUrl = process.env.WEB_URL || 'http://localhost:5173';

    // Check if this is for updating an existing asset (version mode)
    const isVersionMode = req.query.mode === 'version';
    const completeUrl = isVersionMode
      ? `${webUrl}/publish/${draftId}/select-asset`
      : `${webUrl}/publish/${draftId}`;

    res.status(201).json({
      draftId,
      completeUrl,
      mode: isVersionMode ? 'version' : 'new',
      expiresAt: expiresAt.toISOString(),
      thumbnailUrl,
      hasScreenshot: !!thumbnailUrl,
      prefilled: {
        name: !!prefillName,
        description: !!prefillDescription,
        tags: prefillTags.length > 0,
        mediaCount: mediaArray.length,
      },
      metadata: {
        context: assetMeta.context,
        nodeCount: assetMeta.nodeCount,
        nodeNames: assetMeta.nodeNames,
        nodeTypes: assetMeta.nodeTypes,
        houdiniVersion: assetMeta.houdiniVersion,
        hasHdaDependencies: assetMeta.hasHdaDependencies,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /drafts/:draftId
 * Get draft details (for the web UI to display)
 */
router.get('/:draftId', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;

    const result = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = result.rows[0];

    // Check ownership
    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only access your own drafts');
    }

    // Load package to check asset type
    const packageData = await loadDraftPackage(draftId);
    const isHda = packageData?.format === 'sopdrop-hda-v1';
    const isVex = packageData?.format === 'sopdrop-vex-v1';
    const assetType = isHda ? 'hda' : isVex ? 'vex' : 'node';

    // Build response
    const response = {
      draftId: draft.draft_id,
      context: draft.houdini_context,
      nodeCount: draft.node_count,
      nodeNames: draft.node_names,
      nodeTypes: draft.node_types,
      houdiniVersion: draft.houdini_version,
      hasHdaDependencies: draft.has_hda_dependencies,
      dependencies: draft.dependencies,
      name: draft.name,
      description: draft.description,
      thumbnailUrl: draft.thumbnail_url,
      media: draft.media || [],
      expiresAt: draft.expires_at,
      createdAt: draft.created_at,
      // Asset type info
      assetType,
    };

    // Add HDA-specific metadata
    if (isHda && packageData.metadata) {
      response.hdaInfo = {
        typeName: packageData.metadata.type_name,
        typeLabel: packageData.metadata.type_label,
        hdaVersion: packageData.metadata.hda_version,
        icon: packageData.metadata.icon,
        fileName: packageData.metadata.file_name,
        fileSize: packageData.metadata.file_size,
        hasHelp: packageData.metadata.has_help,
      };
    }

    res.json(response);
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /drafts/:draftId
 * Update draft metadata (name, description, tags, etc.)
 */
router.put('/:draftId', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;
    const {
      name: rawName,
      description: rawDescription,
      tags: rawTags,
      license,
      isPublic,
    } = req.body;

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only edit your own drafts');
    }

    // Sanitize inputs
    const name = rawName ? sanitizePlainText(rawName, 100) : draft.name;
    const description = rawDescription !== undefined ? sanitizeMarkdown(rawDescription, 2000) : draft.description;
    const tags = rawTags ? sanitizeTags(rawTags) : draft.tags;

    // Map isPublic boolean to visibility string
    const visibility = isPublic === false ? 'private' : (draft.visibility || 'public');

    // Update draft
    await query(`
      UPDATE asset_drafts
      SET name = $1, description = $2, tags = $3, license = $4, visibility = $5, updated_at = NOW()
      WHERE draft_id = $6
    `, [name, description, tags, license || draft.license, visibility, draftId]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /drafts/:draftId/thumbnail
 * Upload thumbnail image for draft
 */
router.post('/:draftId/thumbnail', authenticate, uploadThumbnail.single('thumbnail'), validateUploads(), async (req, res, next) => {
  let uploadedKey = null;

  try {
    const { draftId } = req.params;

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only edit your own drafts');
    }

    if (!req.file) {
      throw new ValidationError('Thumbnail image is required');
    }

    // Upload to storage
    const uuid = uuidv4();
    const ext = req.file.originalname ? ('.' + req.file.originalname.split('.').pop()) : '.jpg';
    const filename = `${uuid}${ext}`;
    const key = `thumbnails/${filename}`;
    uploadedKey = key;

    await storage.upload(key, req.file.buffer, req.file.mimetype);

    // Delete old thumbnail if exists
    if (draft.thumbnail_url) {
      const oldKey = storage.pathToKey(draft.thumbnail_url);
      if (oldKey) {
        await storage.remove(oldKey);
      }
    }

    const thumbnailUrl = storage.keyToPath(key);

    // Update draft with thumbnail
    await query(`
      UPDATE asset_drafts
      SET thumbnail_url = $1, updated_at = NOW()
      WHERE draft_id = $2
    `, [thumbnailUrl, draftId]);

    res.json({
      success: true,
      thumbnailUrl,
    });
  } catch (error) {
    // Clean up uploaded file on error
    if (uploadedKey) {
      await storage.remove(uploadedKey);
    }
    next(error);
  }
});

/**
 * POST /drafts/:draftId/media
 * Upload media files (images/videos) for draft
 */
router.post('/:draftId/media', authenticate, uploadMedia.array('files', 10), validateUploads(), async (req, res, next) => {
  const uploadedKeys = [];

  try {
    const { draftId } = req.params;

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only edit your own drafts');
    }

    if (!req.files || req.files.length === 0) {
      throw new ValidationError('No files uploaded');
    }

    // Parse captions from body (JSON array)
    let captions = [];
    if (req.body.captions) {
      try {
        captions = JSON.parse(req.body.captions);
      } catch (e) {
        // Ignore parsing errors
      }
    }

    // Upload files to storage and build media array
    const newMedia = [];
    for (let i = 0; i < req.files.length; i++) {
      const file = req.files[i];
      const fileUuid = uuidv4();
      const ext = file.originalname ? ('.' + file.originalname.split('.').pop()) : '';
      const filename = `${fileUuid}${ext}`;
      const key = `media/${filename}`;

      await storage.upload(key, file.buffer, file.mimetype);
      uploadedKeys.push(key);

      newMedia.push({
        url: storage.keyToPath(key),
        type: file.mimetype.startsWith('video/') ? 'video' : 'image',
        caption: captions[i] || null,
        filename: file.originalname,
        size: file.size,
      });
    }

    // Get existing media and append new files
    const existingMedia = draft.media || [];
    const updatedMedia = [...existingMedia, ...newMedia];

    // Update draft with new media array
    await query(`
      UPDATE asset_drafts
      SET media = $1, updated_at = NOW()
      WHERE draft_id = $2
    `, [JSON.stringify(updatedMedia), draftId]);

    res.status(201).json({
      success: true,
      media: updatedMedia,
    });
  } catch (error) {
    // Clean up uploaded files on error
    for (const key of uploadedKeys) {
      await storage.remove(key);
    }
    next(error);
  }
});

/**
 * DELETE /drafts/:draftId/media/:index
 * Remove a media item from a draft
 */
router.delete('/:draftId/media/:index', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;
    const mediaIndex = parseInt(req.params.index);

    if (isNaN(mediaIndex) || mediaIndex < 0) {
      throw new ValidationError('Invalid media index');
    }

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only edit your own drafts');
    }

    const existingMedia = draft.media || [];

    if (mediaIndex >= existingMedia.length) {
      throw new NotFoundError('Media item not found');
    }

    // Remove the media item
    const removedMedia = existingMedia[mediaIndex];
    const updatedMedia = existingMedia.filter((_, i) => i !== mediaIndex);

    // Update draft
    await query(`
      UPDATE asset_drafts
      SET media = $1, updated_at = NOW()
      WHERE draft_id = $2
    `, [JSON.stringify(updatedMedia), draftId]);

    // Delete the file from storage
    if (removedMedia.url) {
      const key = storage.pathToKey(removedMedia.url);
      if (key) {
        await storage.remove(key);
      }
    }

    res.json({
      success: true,
      media: updatedMedia,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /drafts/:draftId/publish
 * Publish the draft as a live asset
 *
 * Requires:
 * - Name
 * - Thumbnail (image is required)
 */
router.post('/:draftId/publish', authenticate, requireScope('write'), async (req, res, next) => {
  const client = await getClient();

  try {
    await client.query('BEGIN');

    const { draftId } = req.params;

    // Get draft
    const draftResult = await client.query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    // Verify ownership
    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only publish your own drafts');
    }

    // Validate required fields
    if (!draft.name) {
      throw new ValidationError('Asset name is required. Please complete the listing first.');
    }

    const draftVisibility = draft.visibility || 'public';
    if (!draft.thumbnail_url && draftVisibility === 'public') {
      throw new ValidationError('Thumbnail image is required for public assets. Please upload a screenshot or preview image.');
    }

    // Load the package data
    const packageData = await loadDraftPackage(draftId);
    if (!packageData) {
      throw new ValidationError('Draft package file not found. Please create a new draft.');
    }

    // Detect if this is an HDA package
    const isHda = packageData.format === 'sopdrop-hda-v1';

    // Create slug
    const slug = slugify(draft.name);

    // Check for duplicate slug
    const existingResult = await client.query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.id = $1 AND a.slug = $2
    `, [req.user.id, slug]);

    if (existingResult.rows.length > 0) {
      throw new ValidationError(`You already have an asset named "${draft.name}". Please choose a different name.`);
    }

    // Move package from drafts to permanent storage
    const uuid = uuidv4();
    let filePath, fileSize, fileHash, content;

    if (isHda) {
      // HDA: Decode base64 and upload as binary .hda file
      const hdaBuffer = Buffer.from(packageData.data, 'base64');
      const hdaKey = `hdas/${uuid}.hda`;

      await storage.upload(hdaKey, hdaBuffer, 'application/octet-stream');

      fileSize = hdaBuffer.length;
      fileHash = packageData.checksum; // Already have checksum from client
      filePath = storage.keyToPath(hdaKey);
    } else {
      // Node snippet: Upload as .sopdrop JSON
      content = JSON.stringify(packageData, null, 2);
      const nodeKey = `nodes/${uuid}.sopdrop`;

      await storage.upload(nodeKey, content, 'application/json');

      fileSize = Buffer.byteLength(content, 'utf-8');
      fileHash = calculateHashFromString(content);
      filePath = storage.keyToPath(nodeKey);
    }

    // Build metadata based on asset type
    const isVexPublish = packageData?.format === 'sopdrop-vex-v1';
    const assetType = isHda ? 'hda' : isVexPublish ? 'vex' : 'node';
    const metadata = isHda
      ? {
          hdaTypeName: packageData.metadata?.type_name,
          hdaTypeLabel: packageData.metadata?.type_label,
          hdaVersion: packageData.metadata?.hda_version,
          hdaIcon: packageData.metadata?.icon,
          hdaFileName: packageData.metadata?.file_name,
          hdaHasHelp: packageData.metadata?.has_help || false,
        }
      : isVexPublish
      ? {
          snippetType: packageData.metadata?.snippet_type || 'wrangle',
          lineCount: packageData.metadata?.line_count || 0,
          hasIncludes: packageData.metadata?.has_includes || false,
        }
      : {
          nodeTypes: draft.node_types,
          hasHdaDependencies: draft.has_hda_dependencies,
          dependencies: draft.dependencies,
          nodeGraph: packageData.metadata?.node_graph || null,
        };

    // Use visibility from draft (already resolved above)
    const isPublic = draftVisibility === 'public';

    // Create asset (including media from draft)
    const assetResult = await client.query(`
      INSERT INTO assets (
        name, slug, owner_id, asset_type, houdini_context,
        description, license, tags, is_public, visibility, latest_version,
        metadata, media
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, '1.0.0', $11, $12)
      RETURNING *
    `, [
      draft.name,
      slug,
      req.user.id,
      assetType,
      draft.houdini_context,
      draft.description || '',
      draft.license || 'mit',
      draft.tags || [],
      isPublic,
      draftVisibility,
      JSON.stringify(metadata),
      JSON.stringify(draft.media || []),
    ]);

    const asset = assetResult.rows[0];

    // Create initial version
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        min_houdini_version, published_by,
        node_count, node_names, thumbnail_url
      )
      VALUES ($1, '1.0.0', $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *
    `, [
      asset.id,
      filePath,
      fileHash,
      fileSize,
      draft.houdini_version,
      req.user.id,
      draft.node_count,
      draft.node_names,
      draft.thumbnail_url,
    ]);

    const version = versionResult.rows[0];

    // Update asset with latest version
    await client.query(`
      UPDATE assets SET latest_version_id = $1 WHERE id = $2
    `, [version.id, asset.id]);

    // Increment user's asset count
    await client.query(`
      UPDATE users SET asset_count = asset_count + 1 WHERE id = $1
    `, [req.user.id]);

    // Auto-save to user's library
    await client.query(`
      INSERT INTO saved_assets (user_id, asset_id, version_id, source)
      VALUES ($1, $2, $3, 'manual')
      ON CONFLICT (user_id, asset_id) DO UPDATE SET
        version_id = EXCLUDED.version_id,
        saved_at = NOW()
    `, [req.user.id, asset.id, version.id]);

    // Delete the draft
    await client.query(`
      DELETE FROM asset_drafts WHERE draft_id = $1
    `, [draftId]);

    // Delete draft package file
    await deleteDraftPackage(draftId);

    await client.query('COMMIT');

    res.status(201).json({
      success: true,
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: `${req.user.username}/${asset.slug}`,
        type: asset.asset_type,
        context: asset.houdini_context,
        version: '1.0.0',
        nodeCount: draft.node_count,
        thumbnailUrl: draft.thumbnail_url,
        createdAt: asset.created_at,
      },
    });
  } catch (error) {
    await client.query('ROLLBACK');
    next(error);
  } finally {
    client.release();
  }
});

/**
 * DELETE /drafts/:draftId
 * Delete a draft (cancel publish)
 */
router.delete('/:draftId', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts WHERE draft_id = $1
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only delete your own drafts');
    }

    // Delete thumbnail if exists
    if (draft.thumbnail_url) {
      const thumbnailKey = storage.pathToKey(draft.thumbnail_url);
      if (thumbnailKey) {
        await storage.remove(thumbnailKey);
      }
    }

    // Delete media files if they exist
    const media = draft.media || [];
    for (const item of media) {
      if (item.url) {
        const mediaKey = storage.pathToKey(item.url);
        if (mediaKey) {
          await storage.remove(mediaKey);
        }
      }
    }

    // Delete package file
    await deleteDraftPackage(draftId);

    // Delete from database
    await query(`
      DELETE FROM asset_drafts WHERE draft_id = $1
    `, [draftId]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /drafts/:draftId/select-asset
 * Convert a regular draft to a version draft by selecting a target asset
 *
 * This is used when the user wants to publish a draft as a new version
 * of an existing asset instead of creating a new asset.
 */
router.post('/:draftId/select-asset', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;
    const { assetId } = req.body;

    if (!assetId) {
      throw new ValidationError('Asset ID is required');
    }

    // Get draft and verify ownership
    const draftResult = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Draft not found or expired');
    }

    const draft = draftResult.rows[0];

    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only modify your own drafts');
    }

    // Check if already a version draft
    if (draft.name && draft.name.startsWith('__VERSION_DRAFT__:')) {
      throw new ValidationError('Draft is already associated with an asset');
    }

    // Get the target asset and verify ownership
    const assetResult = await query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE a.asset_id = $1
    `, [assetId]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    if (asset.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only update your own assets');
    }

    // Optionally validate context match
    if (draft.houdini_context !== asset.houdini_context) {
      throw new ValidationError(
        `Context mismatch: Draft is ${draft.houdini_context.toUpperCase()}, ` +
        `but asset "${asset.name}" is ${asset.houdini_context.toUpperCase()}`
      );
    }

    // Convert to version draft by setting the special name marker
    await query(`
      UPDATE asset_drafts
      SET name = $1, updated_at = NOW()
      WHERE draft_id = $2
    `, [`__VERSION_DRAFT__:${assetId}`, draftId]);

    // Return the redirect URL
    const webUrl = process.env.WEB_URL || 'http://localhost:5173';

    res.json({
      success: true,
      redirectUrl: `${webUrl}/publish/${draftId}/version`,
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: `${asset.username}/${asset.slug}`,
        currentVersion: asset.latest_version || '1.0.0',
        context: asset.houdini_context,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /drafts
 * List user's drafts
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT * FROM asset_drafts
      WHERE owner_id = $1 AND expires_at > NOW()
      ORDER BY created_at DESC
    `, [req.user.id]);

    res.json({
      drafts: result.rows.map(d => ({
        draftId: d.draft_id,
        name: d.name,
        context: d.houdini_context,
        nodeCount: d.node_count,
        thumbnailUrl: d.thumbnail_url,
        expiresAt: d.expires_at,
        createdAt: d.created_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /version-drafts
 * Create a draft for a new version of an existing asset
 *
 * Similar to /drafts but for updating existing assets instead of creating new ones.
 */
router.post('/version', authenticate, requireScope('write'), uploadLimiter, async (req, res, next) => {
  try {
    const { package: chopPackage, screenshot, assetId } = req.body;

    if (!chopPackage) {
      throw new ValidationError('Package data is required');
    }

    if (!assetId) {
      throw new ValidationError('Asset ID is required');
    }

    // Parse package if it's a string
    let packageData;
    try {
      packageData = typeof chopPackage === 'string' ? JSON.parse(chopPackage) : chopPackage;
    } catch (e) {
      throw new ValidationError('Invalid package JSON');
    }

    // Validate the .sopdrop package
    validateChopPackage(packageData);

    // Get the asset and verify ownership
    const assetResult = await query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE a.asset_id = $1
    `, [assetId]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    if (asset.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only update your own assets');
    }

    // Extract metadata for preview
    const assetMeta = extractAssetMetadata(packageData);

    // Generate draft ID
    const draftId = uuidv4();

    // Save package to storage
    const { filePath, fileSize } = await saveDraftPackage(packageData, draftId);

    // Save screenshot if provided
    let thumbnailUrl = null;
    if (screenshot) {
      thumbnailUrl = await saveScreenshotFromBase64(screenshot, draftId);
    }

    // Store version draft metadata
    const expiresAt = new Date(Date.now() + DRAFT_EXPIRATION_MS);

    // Store in asset_drafts but with a reference to the target asset
    await query(`
      INSERT INTO asset_drafts (
        draft_id, owner_id, file_path, file_size,
        houdini_context, node_count, node_names, node_types,
        houdini_version, has_hda_dependencies, dependencies,
        expires_at, thumbnail_url, name
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
    `, [
      draftId,
      req.user.id,
      filePath,
      fileSize,
      assetMeta.context,
      assetMeta.nodeCount,
      assetMeta.nodeNames,
      assetMeta.nodeTypes,
      assetMeta.houdiniVersion,
      assetMeta.hasHdaDependencies,
      JSON.stringify(assetMeta.dependencies || []),
      expiresAt,
      thumbnailUrl,
      `__VERSION_DRAFT__:${assetId}`,  // Special marker to identify version drafts
    ]);

    // Return draft ID and URL for browser completion
    const webUrl = process.env.WEB_URL || 'http://localhost:5173';
    const completeUrl = `${webUrl}/publish/version/${draftId}?asset=${asset.slug}`;

    res.status(201).json({
      draftId,
      completeUrl,
      expiresAt: expiresAt.toISOString(),
      thumbnailUrl,
      hasScreenshot: !!thumbnailUrl,
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: `${asset.username}/${asset.slug}`,
        currentVersion: asset.latest_version || '1.0.0',
        context: asset.houdini_context,
      },
      metadata: {
        context: assetMeta.context,
        nodeCount: assetMeta.nodeCount,
        houdiniVersion: assetMeta.houdiniVersion,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /version-drafts/:draftId
 * Get version draft details
 */
router.get('/version/:draftId', authenticate, async (req, res, next) => {
  try {
    const { draftId } = req.params;

    const result = await query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Version draft not found or expired');
    }

    const draft = result.rows[0];

    // Check ownership
    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only access your own drafts');
    }

    // Parse the asset ID from the name field
    if (!draft.name || !draft.name.startsWith('__VERSION_DRAFT__:')) {
      throw new ValidationError('Invalid version draft');
    }

    const targetAssetId = draft.name.replace('__VERSION_DRAFT__:', '');

    // Get the target asset
    const assetResult = await query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE a.asset_id = $1
    `, [targetAssetId]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Target asset not found');
    }

    const asset = assetResult.rows[0];

    res.json({
      draftId: draft.draft_id,
      context: draft.houdini_context,
      nodeCount: draft.node_count,
      nodeNames: draft.node_names,
      nodeTypes: draft.node_types,
      houdiniVersion: draft.houdini_version,
      hasHdaDependencies: draft.has_hda_dependencies,
      dependencies: draft.dependencies,
      thumbnailUrl: draft.thumbnail_url,
      expiresAt: draft.expires_at,
      createdAt: draft.created_at,
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: `${asset.username}/${asset.slug}`,
        currentVersion: asset.latest_version || '1.0.0',
        context: asset.houdini_context,
        description: asset.description,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /version-drafts/:draftId/publish
 * Publish the version draft as a new version
 */
router.post('/version/:draftId/publish', authenticate, requireScope('write'), async (req, res, next) => {
  const client = await getClient();

  try {
    await client.query('BEGIN');

    const { draftId } = req.params;
    const { version: rawVersion, changelog: rawChangelog } = req.body;

    // Sanitize inputs
    const changelog = rawChangelog ? sanitizeMarkdown(rawChangelog, 10000) : null;

    // Get draft
    const draftResult = await client.query(`
      SELECT * FROM asset_drafts
      WHERE draft_id = $1 AND expires_at > NOW()
    `, [draftId]);

    if (draftResult.rows.length === 0) {
      throw new NotFoundError('Version draft not found or expired');
    }

    const draft = draftResult.rows[0];

    // Verify ownership
    if (draft.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only publish your own drafts');
    }

    // Parse the asset ID from the name field
    if (!draft.name || !draft.name.startsWith('__VERSION_DRAFT__:')) {
      throw new ValidationError('Invalid version draft');
    }

    const targetAssetId = draft.name.replace('__VERSION_DRAFT__:', '');

    // Get the target asset with lock
    const assetResult = await client.query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE a.asset_id = $1
      FOR UPDATE
    `, [targetAssetId]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Target asset not found');
    }

    const asset = assetResult.rows[0];

    // Verify ownership of asset
    if (asset.owner_id !== req.user.id) {
      throw new ForbiddenError('You can only update your own assets');
    }

    // Calculate next version if not provided
    let version = rawVersion;
    if (!version) {
      const currentVersion = asset.latest_version || '1.0.0';
      const parts = currentVersion.split('.');
      const patch = parseInt(parts[2] || 0) + 1;
      version = `${parts[0]}.${parts[1]}.${patch}`;
    }

    // Validate version format
    const versionMatch = version.match(/^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?$/);
    if (!versionMatch) {
      throw new ValidationError('Invalid version format. Use semver (e.g., 1.0.0, 2.1.0-beta)');
    }

    // Check version doesn't exist
    const existingVersion = await client.query(`
      SELECT id FROM versions WHERE asset_id = $1 AND version = $2
    `, [asset.id, version]);

    if (existingVersion.rows.length > 0) {
      throw new ValidationError(`Version ${version} already exists. Versions are immutable.`);
    }

    // Load the package data
    const packageData = await loadDraftPackage(draftId);
    if (!packageData) {
      throw new ValidationError('Draft package file not found. Please create a new draft.');
    }

    // Upload package to permanent storage
    const uuid = uuidv4();
    const content = JSON.stringify(packageData, null, 2);
    const nodeKey = `nodes/${uuid}.sopdrop`;

    await storage.upload(nodeKey, content, 'application/json');

    const fileSize = Buffer.byteLength(content, 'utf-8');
    const fileHash = calculateHashFromString(content);
    const filePath = storage.keyToPath(nodeKey);

    // Create new version
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        changelog, min_houdini_version, published_by,
        node_count, node_names, thumbnail_url, houdini_version
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
      RETURNING *
    `, [
      asset.id,
      version,
      filePath,
      fileHash,
      fileSize,
      changelog,
      draft.houdini_version,
      req.user.id,
      draft.node_count,
      draft.node_names,
      draft.thumbnail_url,
      draft.houdini_version,
    ]);

    const newVersion = versionResult.rows[0];

    // Update asset with latest version
    await client.query(`
      UPDATE assets
      SET latest_version_id = $1, latest_version = $2, updated_at = NOW()
      WHERE id = $3
    `, [newVersion.id, version, asset.id]);

    // Auto-save to user's library
    await client.query(`
      INSERT INTO saved_assets (user_id, asset_id, version_id, source)
      VALUES ($1, $2, $3, 'manual')
      ON CONFLICT (user_id, asset_id) DO UPDATE SET
        version_id = EXCLUDED.version_id,
        saved_at = NOW()
    `, [req.user.id, asset.id, newVersion.id]);

    // Delete the draft
    await client.query(`
      DELETE FROM asset_drafts WHERE draft_id = $1
    `, [draftId]);

    // Delete draft package file
    await deleteDraftPackage(draftId);

    await client.query('COMMIT');

    res.status(201).json({
      success: true,
      version: {
        id: newVersion.version_id,
        version: newVersion.version,
        changelog: newVersion.changelog,
        publishedAt: newVersion.published_at,
      },
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: `${asset.username}/${asset.slug}`,
      },
    });
  } catch (error) {
    await client.query('ROLLBACK');
    next(error);
  } finally {
    client.release();
  }
});

/**
 * Cleanup expired drafts (should be called periodically)
 */
export async function cleanupExpiredDrafts() {
  try {
    // Get expired drafts
    const result = await query(`
      SELECT draft_id, thumbnail_url, media FROM asset_drafts
      WHERE expires_at < NOW()
    `);

    for (const draft of result.rows) {
      // Delete thumbnail
      if (draft.thumbnail_url) {
        const thumbnailKey = storage.pathToKey(draft.thumbnail_url);
        if (thumbnailKey) {
          await storage.remove(thumbnailKey);
        }
      }

      // Delete media files
      const media = draft.media || [];
      for (const item of media) {
        if (item.url) {
          const mediaKey = storage.pathToKey(item.url);
          if (mediaKey) {
            await storage.remove(mediaKey);
          }
        }
      }

      // Delete package file
      await deleteDraftPackage(draft.draft_id);
    }

    // Delete from database
    await query(`
      DELETE FROM asset_drafts WHERE expires_at < NOW()
    `);

    console.log(`Cleaned up ${result.rows.length} expired drafts`);
  } catch (error) {
    console.error('Error cleaning up drafts:', error);
  }
}

export default router;
