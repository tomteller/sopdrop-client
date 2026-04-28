/**
 * Version routes - Immutable version management
 */

import { Router } from 'express';
import multer from 'multer';
import path from 'path';
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import { query, getClient } from '../models/db.js';
import { authenticate, optionalAuth, requireScope } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError, ConflictError } from '../middleware/errorHandler.js';
import { versionLimiter, logAssetEvent, sanitizeMarkdown } from '../middleware/security.js';
import storage from '../services/storage.js';
import { validateUploads } from '../services/fileValidation.js';

const router = Router();

/**
 * Middleware: Require verified email for publishing
 */
async function requireVerifiedEmail(req, res, next) {
  // Skip verification check only in non-production development/testing
  if (process.env.SKIP_EMAIL_VERIFICATION === 'true' && process.env.NODE_ENV !== 'production') {
    return next();
  }

  const result = await query('SELECT email_verified FROM users WHERE id = $1', [req.user.id]);

  if (!result.rows[0]?.email_verified) {
    return next(new ForbiddenError('Email verification required to publish. Check your inbox or request a new verification email.'));
  }

  next();
}

// File upload configuration (memoryStorage: files available as req.file.buffer)
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 50 * 1024 * 1024 },
});

/**
 * Calculate hash from a buffer
 */
function calculateHashFromBuffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

/**
 * Parse and validate semver
 */
function parseSemver(version) {
  const match = version.match(/^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?$/);
  if (!match) {
    return null;
  }
  return {
    major: parseInt(match[1]),
    minor: parseInt(match[2]),
    patch: parseInt(match[3]),
    prerelease: match[4] || null,
  };
}

/**
 * Compare semver versions
 */
function compareSemver(a, b) {
  const va = parseSemver(a);
  const vb = parseSemver(b);

  if (!va || !vb) return 0;

  if (va.major !== vb.major) return va.major - vb.major;
  if (va.minor !== vb.minor) return va.minor - vb.minor;
  if (va.patch !== vb.patch) return va.patch - vb.patch;

  // Prerelease versions are lower than release
  if (va.prerelease && !vb.prerelease) return -1;
  if (!va.prerelease && vb.prerelease) return 1;

  return 0;
}

/**
 * GET /assets/:slug/versions
 * List all versions of an asset
 */
router.get('/:slug(*)/versions', optionalAuth, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get asset
    const assetResult = await query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    // Check visibility
    if (!asset.is_public) {
      if (!req.user || (req.user.id !== asset.owner_id && !req.user.isAdmin)) {
        throw new NotFoundError('Asset not found');
      }
    }

    // Get versions
    const result = await query(`
      SELECT
        v.version_id,
        v.version,
        v.file_size,
        v.changelog,
        v.houdini_version,
        v.houdini_license,
        v.min_houdini_version,
        v.max_houdini_version,
        v.node_count,
        v.download_count,
        v.published_at,
        u.username as published_by
      FROM versions v
      LEFT JOIN users u ON v.published_by = u.id
      WHERE v.asset_id = $1
      ORDER BY v.published_at DESC
    `, [asset.id]);

    res.json({
      asset: {
        name: asset.name,
        slug: `${owner}/${assetSlug}`,
        latestVersion: asset.latest_version,
      },
      versions: result.rows.map(v => ({
        id: v.version_id,
        version: v.version,
        fileSize: v.file_size,
        changelog: v.changelog,
        houdiniVersion: v.houdini_version,
        houdiniLicense: v.houdini_license,
        minHoudiniVersion: v.min_houdini_version,
        maxHoudiniVersion: v.max_houdini_version,
        nodeCount: v.node_count,
        downloadCount: v.download_count,
        publishedAt: v.published_at,
        publishedBy: v.published_by,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /assets/:slug/versions/:version
 * Get a specific version
 */
router.get('/:slug(*)/versions/:version', optionalAuth, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const version = req.params.version;
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    const result = await query(`
      SELECT
        a.name,
        a.slug,
        a.is_public,
        a.owner_id,
        u.username as owner,
        v.*,
        pub.username as published_by_name
      FROM versions v
      JOIN assets a ON v.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN users pub ON v.published_by = pub.id
      WHERE u.username = $1 AND a.slug = $2 AND v.version = $3
    `, [owner.toLowerCase(), assetSlug.toLowerCase(), version]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Version not found');
    }

    const v = result.rows[0];

    // Check visibility
    if (!v.is_public) {
      if (!req.user || (req.user.id !== v.owner_id && !req.user.isAdmin)) {
        throw new NotFoundError('Asset not found');
      }
    }

    res.json({
      id: v.version_id,
      version: v.version,
      asset: {
        name: v.name,
        slug: `${v.owner}/${v.slug}`,
      },
      fileSize: v.file_size,
      fileHash: v.file_hash,
      changelog: v.changelog,
      houdiniVersion: v.houdini_version,
      houdiniLicense: v.houdini_license,
      minHoudiniVersion: v.min_houdini_version,
      maxHoudiniVersion: v.max_houdini_version,
      nodeCount: v.node_count,
      nodeNames: v.node_names,
      code: v.code,
      thumbnailUrl: v.thumbnail_url,
      previewUrl: v.preview_url,
      downloadCount: v.download_count,
      publishedAt: v.published_at,
      publishedBy: v.published_by_name,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /assets/:slug/versions
 * Publish a new version (immutable)
 */
router.post('/:slug(*)/versions', authenticate, requireScope('write'), requireVerifiedEmail, versionLimiter, upload.single('file'), validateUploads(), async (req, res, next) => {
  const client = await getClient();
  let storageKey = null;

  try {
    await client.query('BEGIN');

    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get asset
    const assetResult = await client.query(`
      SELECT a.*, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
      FOR UPDATE
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    // Verify ownership
    if (asset.owner_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only publish versions to your own assets');
    }

    // Validate version
    const { version, changelog: rawChangelog, minHoudiniVersion, maxHoudiniVersion } = req.body;
    const changelog = sanitizeMarkdown(rawChangelog, 10000);

    if (!version) {
      throw new ValidationError('Version is required');
    }

    if (!parseSemver(version)) {
      throw new ValidationError('Invalid version format. Use semver (e.g., 1.0.0, 2.1.0-beta)');
    }

    if (!req.file) {
      throw new ValidationError('File is required');
    }

    // Check version doesn't exist
    const existingVersion = await client.query(`
      SELECT id FROM versions WHERE asset_id = $1 AND version = $2
    `, [asset.id, version]);

    if (existingVersion.rows.length > 0) {
      throw new ConflictError(`Version ${version} already exists. Versions are immutable.`);
    }

    // Check version is greater than latest
    if (asset.latest_version && compareSemver(version, asset.latest_version) <= 0) {
      throw new ValidationError(`Version must be greater than current latest (${asset.latest_version})`);
    }

    // Calculate file hash from buffer
    const fileHash = calculateHashFromBuffer(req.file.buffer);
    const fileSize = req.file.size;

    // Generate storage key and upload
    const ext = path.extname(req.file.originalname) || '.cpio';
    const filename = `${uuidv4()}${ext}`;
    storageKey = `nodes/${filename}`;
    await storage.upload(storageKey, req.file.buffer);

    // Create version
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        changelog, min_houdini_version, max_houdini_version, published_by
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *
    `, [
      asset.id,
      version,
      storage.keyToPath(storageKey),
      fileHash,
      fileSize,
      changelog,
      minHoudiniVersion || asset.min_houdini_version,
      maxHoudiniVersion || asset.max_houdini_version,
      req.user.id,
    ]);

    const newVersion = versionResult.rows[0];

    // Update asset with latest version
    await client.query(`
      UPDATE assets
      SET latest_version_id = $1, latest_version = $2, updated_at = NOW()
      WHERE id = $3
    `, [newVersion.id, version, asset.id]);

    await client.query('COMMIT');

    // Log version publication
    logAssetEvent('version_published', req, {
      targetType: 'version',
      targetId: `${owner}/${assetSlug}@${version}`,
      assetName: asset.name,
      version: version,
      fileSize: newVersion.file_size,
    });

    res.status(201).json({
      id: newVersion.version_id,
      version: newVersion.version,
      asset: {
        name: asset.name,
        slug: `${owner}/${assetSlug}`,
      },
      fileSize: newVersion.file_size,
      fileHash: newVersion.file_hash,
      publishedAt: newVersion.published_at,
    });
  } catch (error) {
    await client.query('ROLLBACK');

    // Clean up uploaded file on error
    if (storageKey) {
      storage.remove(storageKey).catch(() => {});
    }

    next(error);
  } finally {
    client.release();
  }
});

export default router;
