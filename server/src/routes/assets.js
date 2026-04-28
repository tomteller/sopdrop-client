/**
 * Asset routes - Core registry functionality
 *
 * Handles .sopdrop JSON packages for node networks and .hda files for digital assets.
 */

import { Router } from 'express';
import multer from 'multer';
import path from 'path';
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import { query, getClient } from '../models/db.js';
import { authenticate, optionalAuth, requireScope } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { canReadAsset, canWriteAsset, resolveTeamIdBySlug } from '../middleware/teamAccess.js';
import { validateChopPackage, extractAssetMetadata } from '../services/validation.js';
import storage from '../services/storage.js';
import { validateUploads } from '../services/fileValidation.js';
import {
  uploadLimiter,
  downloadLimiter,
  logAssetEvent,
  logAdminEvent,
  sanitizePlainText,
  sanitizeMarkdown,
  sanitizeTags,
} from '../middleware/security.js';

const router = Router();

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
    return next(new ForbiddenError('Email verification required to publish assets. Check your inbox or request a new verification email.'));
  }

  next();
}

// File upload configuration for media (images/videos) - memoryStorage
const uploadMedia = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 50 * 1024 * 1024, // 50MB max for media
  },
  fileFilter: (req, file, cb) => {
    // Allow images and videos
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

// File upload configuration for HDA files only - memoryStorage
const uploadHda = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 100 * 1024 * 1024, // 100MB max for HDAs
  },
  fileFilter: (req, file, cb) => {
    // Allow HDA files and other asset files
    const ext = path.extname(file.originalname).toLowerCase();
    if (['.hda', '.hdanc', '.hdalc', '.cpio', '.zip', '.sopdrop', '.json'].includes(ext)) {
      cb(null, true);
    } else {
      cb(new ValidationError('Only .hda, .cpio, and .zip files are allowed'));
    }
  },
});

// Combined upload for asset file + thumbnail - memoryStorage
const uploadAsset = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 100 * 1024 * 1024,
  },
  fileFilter: (req, file, cb) => {
    if (file.fieldname === 'thumbnail') {
      // Allow images for thumbnails
      if (file.mimetype.startsWith('image/')) {
        cb(null, true);
      } else {
        cb(new ValidationError('Thumbnail must be an image'));
      }
    } else {
      // Allow asset files
      const ext = path.extname(file.originalname).toLowerCase();
      if (['.hda', '.hdanc', '.hdalc', '.cpio', '.zip', '.sopdrop', '.json'].includes(ext)) {
        cb(null, true);
      } else {
        cb(new ValidationError('Only .hda, .cpio, .zip, and .sopdrop files are allowed'));
      }
    }
  },
});

/**
 * Helper: Calculate hash from string content
 */
function calculateHashFromString(content) {
  return crypto.createHash('sha256').update(content).digest('hex');
}

/**
 * Helper: Calculate hash from buffer
 */
function calculateHashFromBuffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

/**
 * Helper: Save .sopdrop package to storage
 */
async function saveChopPackage(packageData, uuid) {
  const content = JSON.stringify(packageData, null, 2);
  const key = `nodes/${uuid}.sopdrop`;

  await storage.upload(key, content, 'application/json');

  return {
    filePath: storage.keyToPath(key),
    fileSize: Buffer.byteLength(content, 'utf-8'),
    fileHash: calculateHashFromString(content),
  };
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
 * Enrich dependency list with Sopdrop asset links where possible.
 * For each dependency, try to find a matching HDA on Sopdrop by type name.
 */
async function enrichDependencies(dependencies) {
  if (!dependencies || !Array.isArray(dependencies) || dependencies.length === 0) {
    return dependencies;
  }

  const enriched = [];
  for (const dep of dependencies) {
    const enrichedDep = { ...dep };

    // Only look up if no slug already set
    if (!enrichedDep.sopdrop_slug && dep.name) {
      try {
        const result = await query(`
          SELECT a.slug, u.username, a.name AS asset_name, a.latest_version
          FROM assets a
          JOIN users u ON a.owner_id = u.id
          WHERE a.asset_type = 'hda'
            AND (a.visibility = 'public' OR (a.visibility IS NULL AND a.is_public = true))
            AND a.is_deprecated = false
            AND (
              a.metadata->>'hdaTypeName' = $1
              OR a.name ILIKE $1
            )
          ORDER BY a.download_count DESC
          LIMIT 1
        `, [dep.name]);

        if (result.rows.length > 0) {
          const match = result.rows[0];
          enrichedDep.sopdrop_slug = `${match.username}/${match.slug}`;
          enrichedDep.sopdrop_name = match.asset_name;
          enrichedDep.sopdrop_version = match.latest_version;
        }
      } catch (e) {
        // Non-critical — skip enrichment for this dep
      }
    }

    enriched.push(enrichedDep);
  }
  return enriched;
}

/**
 * GET /assets
 * List/search assets with improved weighted search
 */
router.get('/', optionalAuth, async (req, res, next) => {
  try {
    const {
      q,           // Search query
      context,     // Houdini context filter
      type,        // Asset type filter
      tags,        // Tags filter (comma-separated)
      user,        // Filter by username
      sort = 'recent',
      limit = 50,
      offset = 0,
    } = req.query;

    // Use visibility column (public only for browsing) - fallback to is_public for migration
    let whereClause = "WHERE (a.visibility = 'public' OR (a.visibility IS NULL AND a.is_public = true)) AND a.is_deprecated = false";
    const params = [];
    let paramIndex = 1;
    let selectRank = '';

    // Improved search query with weighted full-text search
    if (q) {
      // Use the weighted search_vector column if available, otherwise fallback
      selectRank = `, ts_rank(
        COALESCE(a.search_vector,
          setweight(to_tsvector('english', COALESCE(a.name, '')), 'A') ||
          setweight(to_tsvector('english', COALESCE(array_to_string(a.tags, ' '), '')), 'B') ||
          setweight(to_tsvector('english', COALESCE(a.description, '')), 'C')
        ),
        websearch_to_tsquery('english', $${paramIndex})
      ) as rank`;

      whereClause += ` AND (
        COALESCE(a.search_vector,
          setweight(to_tsvector('english', COALESCE(a.name, '')), 'A') ||
          setweight(to_tsvector('english', COALESCE(array_to_string(a.tags, ' '), '')), 'B') ||
          setweight(to_tsvector('english', COALESCE(a.description, '')), 'C')
        ) @@ websearch_to_tsquery('english', $${paramIndex})
        OR a.name ILIKE $${paramIndex + 1}
        OR $${paramIndex} = ANY(a.tags)
      )`;
      params.push(q, `%${q}%`);
      paramIndex += 2;
    }

    // Context filter
    if (context) {
      whereClause += ` AND a.houdini_context = $${paramIndex}`;
      params.push(context.toLowerCase());
      paramIndex++;
    }

    // Type filter
    if (type) {
      whereClause += ` AND a.asset_type = $${paramIndex}`;
      params.push(type.toLowerCase());
      paramIndex++;
    }

    // Tags filter
    if (tags) {
      const tagList = tags.split(',').map(t => t.trim().toLowerCase());
      whereClause += ` AND a.tags && $${paramIndex}::text[]`;
      params.push(tagList);
      paramIndex++;
    }

    // User filter
    if (user) {
      whereClause += ` AND u.username = $${paramIndex}`;
      params.push(user.toLowerCase());
      paramIndex++;
    }

    // Sort order - use relevance if searching
    let orderBy = 'ORDER BY a.created_at DESC';
    if (q && sort === 'relevance') {
      orderBy = 'ORDER BY rank DESC, a.download_count DESC';
    } else if (sort === 'downloads' || sort === 'popular') {
      orderBy = 'ORDER BY a.download_count DESC';
    } else if (sort === 'name') {
      orderBy = 'ORDER BY a.name ASC';
    } else if (sort === 'updated') {
      orderBy = 'ORDER BY a.updated_at DESC';
    } else if (sort === 'favorites') {
      orderBy = 'ORDER BY a.favorite_count DESC';
    }

    // Pagination
    const limitVal = Math.min(parseInt(limit) || 50, 100);
    const offsetVal = parseInt(offset) || 0;

    // Query
    const result = await query(`
      SELECT
        a.id,
        a.asset_id,
        a.name,
        a.slug,
        u.username as owner,
        u.avatar_url as owner_avatar,
        a.asset_type,
        a.houdini_context,
        a.description,
        a.license,
        a.tags,
        a.latest_version,
        a.download_count,
        a.favorite_count,
        a.comment_count,
        a.created_at,
        a.updated_at,
        v.thumbnail_url,
        v.houdini_license
        ${selectRank}
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      ${whereClause}
      ${orderBy}
      LIMIT $${paramIndex} OFFSET $${paramIndex + 1}
    `, [...params, limitVal, offsetVal]);

    // Get total count
    const countResult = await query(`
      SELECT COUNT(DISTINCT a.id) as total
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      ${whereClause}
    `, params);

    res.json({
      assets: result.rows.map(a => ({
        id: a.asset_id,
        name: a.name,
        slug: `${a.owner}/${a.slug}`,
        owner: a.owner,
        ownerAvatar: a.owner_avatar,
        type: a.asset_type,
        context: a.houdini_context,
        description: a.description,
        license: a.license,
        tags: a.tags || [],
        latestVersion: a.latest_version,
        downloadCount: a.download_count,
        favoriteCount: a.favorite_count || 0,
        commentCount: a.comment_count || 0,
        thumbnailUrl: a.thumbnail_url,
        houdiniLicense: a.houdini_license,
        createdAt: a.created_at,
        updatedAt: a.updated_at,
      })),
      total: parseInt(countResult.rows[0].total),
      limit: limitVal,
      offset: offsetVal,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /assets/tags
 * List all tags with usage counts for discovery
 *
 * Query params:
 *   context - Filter tags by Houdini context (sop, vop, etc.)
 *   type - Filter by asset type (node, hda)
 *   q - Search/filter tags by prefix
 *   sort - 'popular' (default) or 'alpha'
 *   limit - Max results (default 50, max 200)
 *
 * NOTE: This route MUST be defined before /:slug(*) to avoid the wildcard capturing it
 */
router.get('/tags', async (req, res, next) => {
  try {
    const {
      context,
      type,
      q,
      sort = 'popular',
      limit = 50,
    } = req.query;

    let whereClause = "WHERE a.is_public = true AND a.is_deprecated = false";
    const params = [];
    let paramIndex = 1;

    // Filter by context
    if (context) {
      whereClause += ` AND a.houdini_context = $${paramIndex}`;
      params.push(context.toLowerCase());
      paramIndex++;
    }

    // Filter by type
    if (type) {
      whereClause += ` AND a.asset_type = $${paramIndex}`;
      params.push(type.toLowerCase());
      paramIndex++;
    }

    // Query to unnest tags and count them
    let tagQuery = `
      SELECT tag, COUNT(*) as count
      FROM (
        SELECT UNNEST(a.tags) as tag
        FROM assets a
        ${whereClause}
      ) AS expanded_tags
    `;

    // Filter by prefix if q provided
    if (q) {
      tagQuery += ` WHERE tag ILIKE $${paramIndex}`;
      params.push(`${q}%`);
      paramIndex++;
    }

    tagQuery += ` GROUP BY tag`;

    // Sort order
    if (sort === 'alpha') {
      tagQuery += ` ORDER BY tag ASC`;
    } else {
      tagQuery += ` ORDER BY count DESC, tag ASC`;
    }

    // Limit
    const limitVal = Math.min(parseInt(limit) || 50, 200);
    tagQuery += ` LIMIT $${paramIndex}`;
    params.push(limitVal);

    const result = await query(tagQuery, params);

    res.json({
      tags: result.rows.map(r => ({
        tag: r.tag,
        count: parseInt(r.count),
      })),
      total: result.rows.length,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /assets/tags/groups
 * Get curated tag groups for browsing
 *
 * Query params:
 *   context - Filter groups relevant to a specific context (sop, vop, etc.)
 *   withCounts - Include usage counts for each tag (default: false)
 *
 * NOTE: This route MUST be defined before /:slug(*) to avoid the wildcard capturing it
 */
router.get('/tags/groups', async (req, res, next) => {
  try {
    const { context, withCounts } = req.query;

    // Get all featured tag groups
    let groupsQuery = `
      SELECT id, name, slug, description, icon, tags, contexts, position
      FROM tag_groups
      WHERE is_featured = true
    `;
    const params = [];

    // Filter by context if provided
    if (context) {
      groupsQuery += ` AND (contexts = '{}' OR $1 = ANY(contexts))`;
      params.push(context.toLowerCase());
    }

    groupsQuery += ` ORDER BY position ASC`;

    const groupsResult = await query(groupsQuery, params);

    // If withCounts requested, get usage counts for each tag
    let tagCounts = {};
    if (withCounts === 'true') {
      // Build where clause for public assets
      let countWhere = "WHERE a.is_public = true AND a.is_deprecated = false";
      const countParams = [];

      if (context) {
        countWhere += ` AND a.houdini_context = $1`;
        countParams.push(context.toLowerCase());
      }

      const countsResult = await query(`
        SELECT tag, COUNT(*) as count
        FROM (
          SELECT UNNEST(a.tags) as tag
          FROM assets a
          ${countWhere}
        ) AS expanded_tags
        GROUP BY tag
      `, countParams);

      tagCounts = Object.fromEntries(
        countsResult.rows.map(r => [r.tag, parseInt(r.count)])
      );
    }

    res.json({
      groups: groupsResult.rows.map(g => ({
        id: g.id,
        name: g.name,
        slug: g.slug,
        description: g.description,
        icon: g.icon,
        contexts: g.contexts || [],
        tags: g.tags.map(tag => ({
          tag,
          count: tagCounts[tag] || 0,
        })),
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /assets/:slug/download/:version
 * Download a specific version
 *
 * For .sopdrop packages (node networks), returns JSON.
 * For HDAs, returns binary file download.
 *
 * NOTE: This route MUST be defined before /:slug(*) to avoid the wildcard capturing /download/
 */
router.get('/:slug(*)/download/:version', optionalAuth, downloadLimiter, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const version = req.params.version;
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get asset and version
    const result = await query(`
      SELECT a.*, v.file_path, v.file_hash, u.username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      JOIN versions v ON v.asset_id = a.id
      WHERE u.username = $1 AND a.slug = $2 AND v.version = $3
    `, [owner.toLowerCase(), assetSlug.toLowerCase(), version]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Version not found');
    }

    const asset = result.rows[0];

    // Check visibility (team members can read their team's private assets)
    if (!(await canReadAsset(req, asset))) {
      throw new NotFoundError('Asset not found');
    }

    // Increment download counts
    await query(`
      UPDATE assets SET download_count = download_count + 1 WHERE id = $1
    `, [asset.id]);

    await query(`
      UPDATE versions SET download_count = download_count + 1
      WHERE asset_id = $1 AND version = $2
    `, [asset.id, version]);

    // Increment owner's download count
    await query(`
      UPDATE users SET download_count = download_count + 1 WHERE id = $1
    `, [asset.owner_id]);

    // Get storage key from DB path
    const key = storage.pathToKey(asset.file_path);

    if (!key || !(await storage.exists(key))) {
      throw new NotFoundError('File not found');
    }

    // Handle different asset types
    if (asset.asset_type === 'node') {
      // .sopdrop package - return as JSON (with size limit)
      const fileBuffer = await storage.download(key);
      const packageContent = fileBuffer.toString('utf-8');
      const packageData = safeJSONParse(packageContent);

      res.json({
        slug: `${owner}/${assetSlug}`,
        version: version,
        owner: owner,
        package: packageData,
        hash: asset.file_hash,
      });
    } else {
      // HDA - binary download via stream
      const ext = path.extname(asset.file_path) || '.hda';
      const filename = `${asset.slug}-${version}${ext}`;
      res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
      res.setHeader('Content-Type', 'application/octet-stream');
      const fileStream = await storage.stream(key);
      fileStream.pipe(res);
    }
  } catch (error) {
    next(error);
  }
});

/**
 * GET /assets/:slug
 * Get a single asset by slug (username/asset-name)
 */
router.get('/:slug(*)', optionalAuth, async (req, res, next) => {
  try {
    // Decode URI in case the slash is encoded as %2F
    const slug = decodeURIComponent(req.params.slug);

    // Parse slug (owner/asset-name)
    const parts = slug.split('/');
    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug. Use format: username/asset-name');
    }

    const [owner, assetSlug] = parts;

    const result = await query(`
      SELECT
        a.*,
        u.username as owner,
        u.display_name as owner_display_name,
        u.avatar_url as owner_avatar,
        v.thumbnail_url,
        v.preview_url,
        v.node_count,
        v.node_names,
        v.houdini_version,
        v.houdini_license,
        v.code,
        v.file_path,
        -- Fork info
        fa.slug as forked_from_slug,
        fu.username as forked_from_owner,
        fa.name as forked_from_name
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      LEFT JOIN assets fa ON a.forked_from_id = fa.id
      LEFT JOIN users fu ON fa.owner_id = fu.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = result.rows[0];

    // Check visibility (team members can read their team's private assets)
    if (!(await canReadAsset(req, asset))) {
      throw new NotFoundError('Asset not found');
    }

    // If code is not in DB, try to read from .sopdrop file (for node assets)
    let code = asset.code;
    if (!code && asset.asset_type === 'node' && asset.file_path) {
      try {
        const key = storage.pathToKey(asset.file_path);
        if (key && await storage.exists(key)) {
          const fileBuffer = await storage.download(key);
          const fileContent = fileBuffer.toString('utf-8');
          const packageData = safeJSONParse(fileContent);
          code = packageData.code || null;
        }
      } catch (e) {
        // Ignore errors reading file
      }
    }

    // Build forked from info if this is a fork
    let forkedFrom = null;
    if (asset.forked_from_slug && asset.forked_from_owner) {
      forkedFrom = {
        slug: `${asset.forked_from_owner}/${asset.forked_from_slug}`,
        owner: asset.forked_from_owner,
        name: asset.forked_from_name,
      };
    }

    res.json({
      id: asset.asset_id,
      name: asset.name,
      slug: `${asset.owner}/${asset.slug}`,
      owner: {
        username: asset.owner,
        displayName: asset.owner_display_name,
        avatarUrl: asset.owner_avatar,
      },
      type: asset.asset_type,
      context: asset.houdini_context,
      description: asset.description,
      readme: asset.readme,
      license: asset.license,
      licenseUrl: asset.license_url,
      tags: asset.tags || [],
      houdiniVersion: asset.houdini_version,
      houdiniLicense: asset.houdini_license,
      minHoudiniVersion: asset.min_houdini_version,
      maxHoudiniVersion: asset.max_houdini_version,
      latestVersion: asset.latest_version,
      downloadCount: asset.download_count,
      favoriteCount: asset.favorite_count || 0,
      commentCount: asset.comment_count || 0,
      forkCount: asset.fork_count || 0,
      forkedFrom: forkedFrom,
      thumbnailUrl: asset.thumbnail_url,
      previewUrl: asset.preview_url,
      nodeCount: asset.node_count,
      nodeNames: asset.node_names,
      nodeTypes: asset.metadata?.nodeTypes || [],
      nodeGraph: asset.metadata?.nodeGraph || null,
      hasHdaDependencies: asset.metadata?.hasHdaDependencies || false,
      dependencies: await enrichDependencies(asset.metadata?.dependencies || []),
      code: code,
      media: asset.media || [],
      isPublic: asset.is_public,
      visibility: asset.visibility || (asset.is_public ? 'public' : 'private'),
      isDeprecated: asset.is_deprecated,
      deprecatedMessage: asset.deprecated_message,
      createdAt: asset.created_at,
      updatedAt: asset.updated_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /assets
 * Publish a new asset (node network as .sopdrop JSON package)
 */
router.post('/', authenticate, requireScope('write'), requireVerifiedEmail, uploadLimiter, async (req, res, next) => {
  const client = await getClient();

  try {
    await client.query('BEGIN');

    const {
      name: rawName,
      description: rawDescription,
      readme: rawReadme,
      license = 'mit',
      tags: rawTags,
      minHoudiniVersion,
      maxHoudiniVersion,
      isPublic = true,
      package: chopPackage,  // The .sopdrop package data
      teamSlug,                // If set, asset is owned by this team
    } = req.body;

    // Sanitize text inputs
    const name = sanitizePlainText(rawName, 100);
    const description = sanitizeMarkdown(rawDescription, 2000);
    const readme = sanitizeMarkdown(rawReadme, 50000);
    const tags = sanitizeTags(rawTags);

    // Validation
    if (!name) {
      throw new ValidationError('Name is required');
    }

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

    // Extract metadata
    const assetMeta = extractAssetMetadata(packageData);

    // Resolve team if uploading into a team library
    let teamId = null;
    if (teamSlug) {
      teamId = await resolveTeamIdBySlug(teamSlug);
      if (!teamId) {
        throw new NotFoundError(`Team not found: ${teamSlug}`);
      }
      const memberCheck = await client.query(
        'SELECT 1 FROM team_members WHERE team_id = $1 AND user_id = $2 LIMIT 1',
        [teamId, req.user.id]
      );
      if (memberCheck.rows.length === 0 && !req.user.isAdmin) {
        throw new ForbiddenError('You are not a member of this team');
      }
    }

    // Create slug
    const slug = slugify(name);

    // Generate UUID and save package
    const uuid = uuidv4();
    const { filePath, fileSize, fileHash } = await saveChopPackage(packageData, uuid);

    // Team-owned assets are never public unless explicitly so
    const finalIsPublic = (isPublic === 'true' || isPublic === true) && !teamId;

    // Create asset
    const assetResult = await client.query(`
      INSERT INTO assets (
        name, slug, owner_id, team_id, asset_type, houdini_context,
        description, readme, license,
        min_houdini_version, max_houdini_version,
        tags, is_public, latest_version,
        metadata
      )
      VALUES ($1, $2, $3, $4, 'node', $5, $6, $7, $8, $9, $10, $11, $12, '1.0.0', $13)
      RETURNING *
    `, [
      name,
      slug,
      req.user.id,
      teamId,
      assetMeta.context,
      description,
      readme,
      license,
      minHoudiniVersion || assetMeta.houdiniVersion,
      maxHoudiniVersion,
      tags,
      finalIsPublic,
      JSON.stringify({
        nodeTypes: assetMeta.nodeTypes,
        hasExpressions: assetMeta.hasExpressions,
        hasPythonSops: assetMeta.hasPythonSops,
        hasHdaDependencies: assetMeta.hasHdaDependencies,
        dependencies: assetMeta.dependencies,
      }),
    ]);

    const asset = assetResult.rows[0];

    // Create initial version (include code for preview)
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        min_houdini_version, max_houdini_version, published_by,
        node_count, node_names, code
      )
      VALUES ($1, '1.0.0', $2, $3, $4, $5, $6, $7, $8, $9, $10)
      RETURNING *
    `, [
      asset.id,
      filePath,
      fileHash,
      fileSize,
      minHoudiniVersion || assetMeta.houdiniVersion,
      maxHoudiniVersion,
      req.user.id,
      assetMeta.nodeCount,
      assetMeta.nodeNames,
      packageData.code, // Store the Python code for previews
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

    await client.query('COMMIT');

    // Log asset creation
    logAssetEvent('asset_created', req, {
      targetType: 'asset',
      targetId: `${req.user.username}/${asset.slug}`,
      assetType: 'node',
      context: asset.houdini_context,
      nodeCount: assetMeta.nodeCount,
    });

    res.status(201).json({
      id: asset.asset_id,
      name: asset.name,
      slug: `${req.user.username}/${asset.slug}`,
      type: asset.asset_type,
      context: asset.houdini_context,
      version: '1.0.0',
      nodeCount: assetMeta.nodeCount,
      createdAt: asset.created_at,
    });
  } catch (error) {
    await client.query('ROLLBACK');
    next(error);
  } finally {
    client.release();
  }
});

/**
 * POST /assets/upload
 * Unified asset upload from web UI - supports file + thumbnail + visibility + folder + category
 */
router.post('/upload', authenticate, requireScope('write'), requireVerifiedEmail, uploadLimiter, uploadAsset.fields([
  { name: 'file', maxCount: 1 },
  { name: 'thumbnail', maxCount: 1 },
]), validateUploads(), async (req, res, next) => {
  const client = await getClient();
  const uploadedKeys = []; // Track uploaded storage keys for cleanup on error

  try {
    await client.query('BEGIN');

    const {
      name: rawName,
      description: rawDescription,
      readme: rawReadme,
      license = 'MIT',
      houdiniContext,
      tags: rawTags,
      minHoudiniVersion,
      maxHoudiniVersion,
      visibility = 'draft',
      folderSlug,
      teamSlug,
    } = req.body;

    // Sanitize text inputs
    const name = sanitizePlainText(rawName, 100);
    const description = sanitizeMarkdown(rawDescription, 2000);
    const readme = sanitizeMarkdown(rawReadme, 50000);

    // Parse tags if JSON string
    let tags = [];
    if (rawTags) {
      try {
        tags = typeof rawTags === 'string' ? JSON.parse(rawTags) : rawTags;
        if (Array.isArray(tags)) {
          tags = sanitizeTags(tags);
        }
      } catch (e) {
        tags = sanitizeTags(rawTags.split(',').map(t => t.trim()));
      }
    }

    // Validation
    if (!name) {
      throw new ValidationError('Name is required');
    }

    if (!req.files?.file?.[0]) {
      throw new ValidationError('Asset file is required');
    }

    const assetFile = req.files.file[0];
    const thumbnailFile = req.files.thumbnail?.[0];

    // Determine asset type from file extension
    const ext = path.extname(assetFile.originalname).toLowerCase();
    let assetType = 'node';
    if (['.hda', '.hdanc', '.hdalc'].includes(ext)) {
      assetType = 'hda';
    }

    // Validate visibility
    const validVisibilities = ['public', 'unlisted', 'private', 'draft'];
    const finalVisibility = validVisibilities.includes(visibility) ? visibility : 'draft';

    // Calculate file hash from buffer
    const fileHash = calculateHashFromBuffer(assetFile.buffer);
    const fileSize = assetFile.size;

    // Create slug
    const slug = slugify(name);

    // Check for slug conflicts
    const existingSlug = await client.query(`
      SELECT id FROM assets WHERE owner_id = $1 AND slug = $2
    `, [req.user.id, slug]);

    if (existingSlug.rows.length > 0) {
      throw new ValidationError('You already have an asset with this name');
    }

    // Resolve team if uploading into a team library. The user must be a
    // member of the team; non-members get a 403. Team-owned assets default
    // to private visibility (only team members can read).
    let teamId = null;
    if (teamSlug) {
      teamId = await resolveTeamIdBySlug(teamSlug);
      if (!teamId) {
        throw new NotFoundError(`Team not found: ${teamSlug}`);
      }
      const memberCheck = await client.query(
        'SELECT 1 FROM team_members WHERE team_id = $1 AND user_id = $2 LIMIT 1',
        [teamId, req.user.id]
      );
      if (memberCheck.rows.length === 0 && !req.user.isAdmin) {
        throw new ForbiddenError('You are not a member of this team');
      }
    }

    // Get folder ID if provided. Folders can be either user-scoped or
    // team-scoped — match accordingly.
    let folderId = null;
    if (folderSlug) {
      const folderResult = teamId
        ? await client.query(
            'SELECT id FROM user_folders WHERE team_id = $1 AND slug = $2',
            [teamId, folderSlug]
          )
        : await client.query(
            'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
            [req.user.id, folderSlug]
          );
      if (folderResult.rows.length > 0) {
        folderId = folderResult.rows[0].id;
      }
    }

    // Determine is_public from visibility for backwards compat. Team
    // assets are never public unless explicitly set.
    const isPublic = finalVisibility === 'public' && !teamId;

    // Upload asset file to storage
    const assetFileUuid = uuidv4();
    const assetFileExt = path.extname(assetFile.originalname) || '.hda';
    const assetFileKey = `hdas/${assetFileUuid}${assetFileExt}`;
    await storage.upload(assetFileKey, assetFile.buffer, assetFile.mimetype);
    uploadedKeys.push(assetFileKey);

    const assetFilePath = storage.keyToPath(assetFileKey);

    // Create asset
    const assetResult = await client.query(`
      INSERT INTO assets (
        name, slug, owner_id, team_id, asset_type, houdini_context,
        description, readme, license,
        min_houdini_version, max_houdini_version,
        tags, is_public, visibility, folder_id, latest_version
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, '1.0.0')
      RETURNING *
    `, [
      name,
      slug,
      req.user.id,
      teamId,
      assetType,
      houdiniContext?.toLowerCase() || 'sop',
      description,
      readme,
      license,
      minHoudiniVersion,
      maxHoudiniVersion,
      tags,
      isPublic,
      finalVisibility,
      folderId,
    ]);

    const asset = assetResult.rows[0];

    // Upload and create thumbnail URL if provided
    let thumbnailUrl = null;
    if (thumbnailFile) {
      const thumbUuid = uuidv4();
      const thumbExt = path.extname(thumbnailFile.originalname) || '.jpg';
      const thumbKey = `thumbnails/${thumbUuid}${thumbExt}`;
      await storage.upload(thumbKey, thumbnailFile.buffer, thumbnailFile.mimetype);
      uploadedKeys.push(thumbKey);
      thumbnailUrl = storage.keyToPath(thumbKey);
    }

    // Create initial version
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        min_houdini_version, max_houdini_version, published_by,
        thumbnail_url
      )
      VALUES ($1, '1.0.0', $2, $3, $4, $5, $6, $7, $8)
      RETURNING *
    `, [
      asset.id,
      assetFilePath,
      fileHash,
      fileSize,
      minHoudiniVersion,
      maxHoudiniVersion,
      req.user.id,
      thumbnailUrl,
    ]);

    const version = versionResult.rows[0];

    // Update asset with latest version
    await client.query(`
      UPDATE assets SET latest_version_id = $1 WHERE id = $2
    `, [version.id, asset.id]);

    // Update folder asset count if in a folder
    if (folderId) {
      await client.query(`
        UPDATE user_folders SET asset_count = asset_count + 1 WHERE id = $1
      `, [folderId]);
    }

    // Only increment public asset count if published
    if (isPublic) {
      await client.query(`
        UPDATE users SET asset_count = asset_count + 1 WHERE id = $1
      `, [req.user.id]);
    }

    // Auto-save to user's library
    await client.query(`
      INSERT INTO saved_assets (user_id, asset_id, version_id, source)
      VALUES ($1, $2, $3, 'manual')
      ON CONFLICT (user_id, asset_id) DO UPDATE SET
        version_id = EXCLUDED.version_id,
        saved_at = NOW()
    `, [req.user.id, asset.id, version.id]);

    await client.query('COMMIT');

    // Log asset creation
    logAssetEvent('asset_created', req, {
      targetType: 'asset',
      targetId: `${req.user.username}/${asset.slug}`,
      assetType: assetType,
      visibility: finalVisibility,
      context: asset.houdini_context,
      fileSize: fileSize,
    });

    res.status(201).json({
      asset: {
        id: asset.asset_id,
        name: asset.name,
        slug: asset.slug,
        type: assetType,
        version: '1.0.0',
        visibility: finalVisibility,
        thumbnailUrl: thumbnailUrl,
        createdAt: asset.created_at,
      },
    });
  } catch (error) {
    await client.query('ROLLBACK');

    // Clean up uploaded files on error
    for (const key of uploadedKeys) {
      await storage.remove(key);
    }

    next(error);
  } finally {
    client.release();
  }
});

/**
 * POST /assets/hda
 * Publish a new HDA (binary file upload)
 */
router.post('/hda', authenticate, requireScope('write'), requireVerifiedEmail, uploadLimiter, uploadHda.single('file'), validateUploads(), async (req, res, next) => {
  const client = await getClient();
  let uploadedKey = null; // Track uploaded storage key for cleanup on error

  try {
    await client.query('BEGIN');

    const {
      name: rawName,
      description: rawDescription,
      readme: rawReadme,
      license = 'mit',
      houdiniContext,
      tags: rawTags,
      minHoudiniVersion,
      maxHoudiniVersion,
      isPublic = true,
    } = req.body;

    // Sanitize text inputs
    const name = sanitizePlainText(rawName, 100);
    const description = sanitizeMarkdown(rawDescription, 2000);
    const readme = sanitizeMarkdown(rawReadme, 50000);
    const tags = sanitizeTags(rawTags);

    // Validation
    if (!name) {
      throw new ValidationError('Name is required');
    }

    if (!req.file) {
      throw new ValidationError('HDA file is required');
    }

    // Create slug
    const slug = slugify(name);

    // Calculate file hash from buffer
    const fileHash = calculateHashFromBuffer(req.file.buffer);
    const fileSize = req.file.size;

    // Upload file to storage
    const fileUuid = uuidv4();
    const ext = path.extname(req.file.originalname) || '.hda';
    const key = `hdas/${fileUuid}${ext}`;
    await storage.upload(key, req.file.buffer, req.file.mimetype);
    uploadedKey = key;

    const filePath = storage.keyToPath(key);

    // Create asset
    const assetResult = await client.query(`
      INSERT INTO assets (
        name, slug, owner_id, asset_type, houdini_context,
        description, readme, license,
        min_houdini_version, max_houdini_version,
        tags, is_public, latest_version
      )
      VALUES ($1, $2, $3, 'hda', $4, $5, $6, $7, $8, $9, $10, $11, '1.0.0')
      RETURNING *
    `, [
      name,
      slug,
      req.user.id,
      houdiniContext?.toLowerCase() || 'sop',
      description,
      readme,
      license,
      minHoudiniVersion,
      maxHoudiniVersion,
      tags,
      isPublic === 'true' || isPublic === true,
    ]);

    const asset = assetResult.rows[0];

    // Create initial version
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        min_houdini_version, max_houdini_version, published_by
      )
      VALUES ($1, '1.0.0', $2, $3, $4, $5, $6, $7)
      RETURNING *
    `, [
      asset.id,
      filePath,
      fileHash,
      fileSize,
      minHoudiniVersion,
      maxHoudiniVersion,
      req.user.id,
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

    await client.query('COMMIT');

    // Log HDA creation
    logAssetEvent('asset_created', req, {
      targetType: 'asset',
      targetId: `${req.user.username}/${asset.slug}`,
      assetType: 'hda',
      context: asset.houdini_context,
      fileSize: fileSize,
    });

    res.status(201).json({
      id: asset.asset_id,
      name: asset.name,
      slug: `${req.user.username}/${asset.slug}`,
      type: 'hda',
      version: '1.0.0',
      createdAt: asset.created_at,
    });
  } catch (error) {
    await client.query('ROLLBACK');

    // Clean up uploaded file on error
    if (uploadedKey) {
      await storage.remove(uploadedKey);
    }

    next(error);
  } finally {
    client.release();
  }
});

/**
 * PUT /assets/:slug
 * Update asset metadata (not files - those are immutable versions)
 */
router.put('/:slug(*)', authenticate, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Verify ownership
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

    if (!(await canWriteAsset(req, asset))) {
      throw new ForbiddenError('You can only edit your own assets (or your team\'s)');
    }

    // Update allowed fields
    const {
      description: rawDescription,
      readme: rawReadme,
      tags: rawTags,
      minHoudiniVersion,
      maxHoudiniVersion,
      isPublic,
      visibility: rawVisibility,
      isDeprecated,
      deprecatedMessage: rawDeprecatedMessage,
    } = req.body;

    const updates = [];
    const values = [];
    let paramIndex = 1;

    if (rawDescription !== undefined) {
      updates.push(`description = $${paramIndex++}`);
      values.push(sanitizeMarkdown(rawDescription, 2000));
    }

    if (rawReadme !== undefined) {
      updates.push(`readme = $${paramIndex++}`);
      values.push(sanitizeMarkdown(rawReadme, 50000));
    }

    if (rawTags !== undefined) {
      updates.push(`tags = $${paramIndex++}`);
      values.push(sanitizeTags(rawTags));
    }

    if (minHoudiniVersion !== undefined) {
      updates.push(`min_houdini_version = $${paramIndex++}`);
      values.push(minHoudiniVersion || null);
    }

    if (maxHoudiniVersion !== undefined) {
      updates.push(`max_houdini_version = $${paramIndex++}`);
      values.push(maxHoudiniVersion || null);
    }

    if (rawVisibility !== undefined) {
      const validVisibilities = ['public', 'unlisted', 'private', 'draft'];
      const vis = validVisibilities.includes(rawVisibility) ? rawVisibility : 'private';
      updates.push(`visibility = $${paramIndex++}`);
      values.push(vis);
      // Keep is_public in sync
      updates.push(`is_public = $${paramIndex++}`);
      values.push(vis === 'public');
    } else if (isPublic !== undefined) {
      updates.push(`is_public = $${paramIndex++}`);
      values.push(isPublic === 'true' || isPublic === true);
      updates.push(`visibility = $${paramIndex++}`);
      values.push((isPublic === 'true' || isPublic === true) ? 'public' : 'private');
    }

    if (isDeprecated !== undefined) {
      updates.push(`is_deprecated = $${paramIndex++}`);
      values.push(isDeprecated === 'true' || isDeprecated === true);
    }

    if (rawDeprecatedMessage !== undefined) {
      updates.push(`deprecated_message = $${paramIndex++}`);
      values.push(sanitizePlainText(rawDeprecatedMessage, 500));
    }

    if (updates.length === 0) {
      throw new ValidationError('No fields to update');
    }

    updates.push(`updated_at = NOW()`);

    const result = await query(`
      UPDATE assets
      SET ${updates.join(', ')}
      WHERE id = $${paramIndex}
      RETURNING *
    `, [...values, asset.id]);

    res.json({
      id: result.rows[0].asset_id,
      name: result.rows[0].name,
      slug: `${owner}/${result.rows[0].slug}`,
      updatedAt: result.rows[0].updated_at,
    });
  } catch (error) {
    next(error);
  }
});

// Thumbnail-only upload multer - memoryStorage
const uploadThumbnail = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 }, // 10MB
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('image/')) {
      cb(null, true);
    } else {
      cb(new ValidationError('Thumbnail must be an image'));
    }
  },
});

/**
 * POST /assets/:owner/:name/thumbnail
 * Upload or replace asset thumbnail
 */
router.post('/:owner/:name/thumbnail', authenticate, uploadThumbnail.single('thumbnail'), validateUploads(), async (req, res, next) => {
  let uploadedKey = null; // Track uploaded key for cleanup on error

  try {
    const owner = req.params.owner;
    const assetSlug = req.params.name;

    // Verify ownership
    const assetResult = await query(`
      SELECT a.*, u.username, v.thumbnail_url as current_thumbnail
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    if (!(await canWriteAsset(req, asset))) {
      throw new ForbiddenError('You can only edit your own assets (or your team\'s)');
    }

    if (!req.file) {
      throw new ValidationError('No thumbnail file provided');
    }

    // Upload thumbnail to storage
    const thumbUuid = uuidv4();
    const thumbExt = path.extname(req.file.originalname) || '.jpg';
    const thumbKey = `thumbnails/${thumbUuid}${thumbExt}`;
    await storage.upload(thumbKey, req.file.buffer, req.file.mimetype);
    uploadedKey = thumbKey;
    const thumbnailUrl = storage.keyToPath(thumbKey);

    // Update the latest version's thumbnail
    if (asset.latest_version_id) {
      await query(`
        UPDATE versions SET thumbnail_url = $1 WHERE id = $2
      `, [thumbnailUrl, asset.latest_version_id]);
    }

    // Delete old thumbnail if it exists
    if (asset.current_thumbnail) {
      const oldKey = storage.pathToKey(asset.current_thumbnail);
      if (oldKey) {
        await storage.remove(oldKey);
      }
    }

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
 * DELETE /assets/:slug/media/:index
 * Remove a media item from an asset
 *
 * NOTE: This route MUST be defined before DELETE /:slug(*) to avoid the wildcard capturing /media/
 */
router.delete('/:slug(*)/media/:index', authenticate, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const mediaIndex = parseInt(req.params.index);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    if (isNaN(mediaIndex) || mediaIndex < 0) {
      throw new ValidationError('Invalid media index');
    }

    const [owner, assetSlug] = parts;

    // Get asset and verify ownership
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

    if (asset.owner_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only delete media from your own assets');
    }

    const existingMedia = asset.media || [];

    if (mediaIndex >= existingMedia.length) {
      throw new NotFoundError('Media item not found');
    }

    // Remove the media item
    const removedMedia = existingMedia[mediaIndex];
    const updatedMedia = existingMedia.filter((_, i) => i !== mediaIndex);

    // Update asset
    await query(`
      UPDATE assets
      SET media = $1, updated_at = NOW()
      WHERE id = $2
    `, [JSON.stringify(updatedMedia), asset.id]);

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
 * DELETE /assets/:slug
 * Unpublish an asset (soft delete)
 */
router.delete('/:slug(*)', authenticate, requireScope('write'), async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Verify ownership
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

    if (!(await canWriteAsset(req, asset))) {
      throw new ForbiddenError('You can only delete your own assets (or your team\'s)');
    }

    // Soft delete (mark as not public, deprecated)
    await query(`
      UPDATE assets
      SET is_public = false, is_deprecated = true, deprecated_message = 'Asset has been removed by owner'
      WHERE id = $1
    `, [asset.id]);

    // Remove from all users' saved libraries
    await query('DELETE FROM saved_assets WHERE asset_id = $1', [asset.id]);

    // Decrement user's asset count
    await query(`
      UPDATE users SET asset_count = GREATEST(asset_count - 1, 0) WHERE id = $1
    `, [asset.owner_id]);

    // Log deletion
    const eventLogger = req.user.id === asset.owner_id ? logAssetEvent : logAdminEvent;
    eventLogger('asset_deleted', req, {
      targetType: 'asset',
      targetId: `${owner}/${assetSlug}`,
      assetName: asset.name,
      deletedByOwner: req.user.id === asset.owner_id,
    });

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /assets/:slug/media
 * Upload media files (images/videos) to an asset
 */
router.post('/:slug(*)/media', authenticate, uploadMedia.array('files', 10), validateUploads(), async (req, res, next) => {
  const uploadedKeys = []; // Track uploaded keys for cleanup on error

  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get asset and verify ownership
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

    if (asset.owner_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only upload media to your own assets');
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
    for (let index = 0; index < req.files.length; index++) {
      const file = req.files[index];
      const mediaUuid = uuidv4();
      const mediaExt = path.extname(file.originalname);
      const mediaKey = `media/${mediaUuid}${mediaExt}`;
      await storage.upload(mediaKey, file.buffer, file.mimetype);
      uploadedKeys.push(mediaKey);

      newMedia.push({
        url: storage.keyToPath(mediaKey),
        type: file.mimetype.startsWith('video/') ? 'video' : 'image',
        caption: captions[index] ? sanitizePlainText(captions[index], 500) : null,
        filename: file.originalname,
        size: file.size,
      });
    }

    // Get existing media and check cap before appending
    const existingMedia = asset.media || [];
    const MAX_MEDIA_ITEMS = 20;
    if (existingMedia.length + newMedia.length > MAX_MEDIA_ITEMS) {
      throw new ValidationError(`Maximum ${MAX_MEDIA_ITEMS} media items per asset (currently ${existingMedia.length})`);
    }
    const updatedMedia = [...existingMedia, ...newMedia];

    // Update asset with new media array
    await query(`
      UPDATE assets
      SET media = $1, updated_at = NOW()
      WHERE id = $2
    `, [JSON.stringify(updatedMedia), asset.id]);

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
 * POST /assets/:slug/fork
 * Fork an asset to your own namespace
 *
 * Creates a copy of the asset under your username with attribution to the original.
 * License rules are enforced - you cannot fork proprietary assets, and some licenses
 * require the fork to maintain the same license.
 */
router.post('/:slug(*)/fork', authenticate, requireScope('write'), async (req, res, next) => {
  const client = await getClient();

  try {
    await client.query('BEGIN');

    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get the original asset
    const originalResult = await client.query(`
      SELECT
        a.*,
        u.username as owner_username,
        v.file_path,
        v.file_hash,
        v.file_size,
        v.thumbnail_url,
        v.node_count,
        v.node_names,
        v.code,
        v.houdini_version,
        v.houdini_license
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE u.username = $1 AND a.slug = $2 AND a.is_public = true
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (originalResult.rows.length === 0) {
      throw new NotFoundError('Asset not found or is not public');
    }

    const original = originalResult.rows[0];

    // Can't fork your own asset
    if (original.owner_id === req.user.id) {
      throw new ValidationError('You cannot fork your own asset');
    }

    // Check license allows forking
    const originalLicense = original.license?.toLowerCase() || 'mit';

    // Block forking of proprietary assets
    if (originalLicense === 'proprietary') {
      throw new ValidationError(
        `This asset's license (Proprietary) does not allow forking.`
      );
    }

    // Determine fork license from rules table (if available)
    const { license: requestedLicense } = req.body;
    let forkLicense = originalLicense;

    try {
      const licenseRulesResult = await client.query(`
        SELECT allowed_license, requires_attribution, requires_same_license
        FROM license_fork_rules
        WHERE source_license = $1
      `, [originalLicense]);

      if (licenseRulesResult.rows.length > 0) {
        const allowedLicenses = licenseRulesResult.rows.map(r => r.allowed_license);
        const requiresSameLicense = licenseRulesResult.rows.some(r => r.requires_same_license);

        if (requiresSameLicense) {
          forkLicense = originalLicense;
        } else if (requestedLicense && allowedLicenses.includes(requestedLicense.toLowerCase())) {
          forkLicense = requestedLicense.toLowerCase();
        }
      }
    } catch (ruleErr) {
      // license_fork_rules table may not exist — default to same license
      console.warn('license_fork_rules lookup failed, using original license:', ruleErr.message);
    }

    // Create unique slug for fork (add -fork or -fork-2, etc.)
    let forkSlug = original.slug;
    let slugSuffix = '';
    let slugCounter = 0;

    while (true) {
      const testSlug = forkSlug + slugSuffix;
      const existingResult = await client.query(`
        SELECT id FROM assets WHERE owner_id = $1 AND slug = $2
      `, [req.user.id, testSlug]);

      if (existingResult.rows.length === 0) {
        forkSlug = testSlug;
        break;
      }

      slugCounter++;
      slugSuffix = slugCounter === 1 ? '-fork' : `-fork-${slugCounter}`;
    }

    // Copy the package file if it exists
    let newFilePath = null;
    let fileHash = original.file_hash;
    let fileSize = original.file_size;

    if (original.file_path) {
      const originalKey = storage.pathToKey(original.file_path);
      if (originalKey && await storage.exists(originalKey)) {
        const ext = path.extname(original.file_path);
        const newUuid = uuidv4();
        const newFileName = `${newUuid}${ext}`;

        let newKey;
        if (original.asset_type === 'hda') {
          newKey = `hdas/${newFileName}`;
        } else {
          newKey = `nodes/${newFileName}`;
        }

        // Download from storage and re-upload with new key
        const fileBuffer = await storage.download(originalKey);
        await storage.upload(newKey, fileBuffer);
        newFilePath = storage.keyToPath(newKey);
      }
    }

    // Create the forked asset
    const forkResult = await client.query(`
      INSERT INTO assets (
        name, slug, owner_id, asset_type, houdini_context,
        description, readme, license,
        min_houdini_version, max_houdini_version,
        tags, is_public, latest_version,
        metadata, forked_from_id
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, '1.0.0', $13, $14)
      RETURNING *
    `, [
      original.name,
      forkSlug,
      req.user.id,
      original.asset_type,
      original.houdini_context,
      original.description,
      original.readme,
      forkLicense,
      original.min_houdini_version,
      original.max_houdini_version,
      original.tags,
      true, // Forks start as public
      original.metadata,
      original.id, // Reference to original
    ]);

    const fork = forkResult.rows[0];

    // Create initial version for fork
    const versionResult = await client.query(`
      INSERT INTO versions (
        asset_id, version, file_path, file_hash, file_size,
        min_houdini_version, max_houdini_version, published_by,
        node_count, node_names, code, thumbnail_url,
        houdini_version, houdini_license
      )
      VALUES ($1, '1.0.0', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
      RETURNING *
    `, [
      fork.id,
      newFilePath,
      fileHash,
      fileSize,
      original.min_houdini_version,
      original.max_houdini_version,
      req.user.id,
      original.node_count,
      original.node_names,
      original.code,
      original.thumbnail_url,
      original.houdini_version,
      original.houdini_license,
    ]);

    const version = versionResult.rows[0];

    // Update fork with latest version
    await client.query(`
      UPDATE assets SET latest_version_id = $1 WHERE id = $2
    `, [version.id, fork.id]);

    // Increment original asset's fork count
    await client.query(`
      UPDATE assets SET fork_count = COALESCE(fork_count, 0) + 1 WHERE id = $1
    `, [original.id]);

    // Increment user's asset count
    await client.query(`
      UPDATE users SET asset_count = asset_count + 1 WHERE id = $1
    `, [req.user.id]);

    // Auto-save to user's library
    await client.query(`
      INSERT INTO saved_assets (user_id, asset_id, version_id, source)
      VALUES ($1, $2, $3, 'forked')
      ON CONFLICT (user_id, asset_id) DO UPDATE SET
        version_id = EXCLUDED.version_id,
        source = 'forked',
        saved_at = NOW()
    `, [req.user.id, fork.id, version.id]);

    await client.query('COMMIT');

    // Log fork event
    logAssetEvent('asset_forked', req, {
      targetType: 'asset',
      targetId: `${req.user.username}/${forkSlug}`,
      forkedFrom: `${owner}/${assetSlug}`,
      originalLicense: originalLicense,
      forkLicense: forkLicense,
    });

    res.status(201).json({
      id: fork.asset_id,
      name: fork.name,
      slug: `${req.user.username}/${forkSlug}`,
      type: fork.asset_type,
      context: fork.houdini_context,
      license: forkLicense,
      version: '1.0.0',
      forkedFrom: {
        slug: `${owner}/${assetSlug}`,
        owner: owner,
        name: original.name,
      },
      createdAt: fork.created_at,
    });
  } catch (error) {
    await client.query('ROLLBACK');
    next(error);
  } finally {
    client.release();
  }
});

/**
 * GET /assets/:slug/forks
 * List all forks of an asset
 */
router.get('/:slug(*)/forks', optionalAuth, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new ValidationError('Invalid asset slug');
    }

    const [owner, assetSlug] = parts;

    // Get the original asset ID
    const originalResult = await query(`
      SELECT a.id, a.fork_count
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (originalResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const original = originalResult.rows[0];

    // Get all public forks
    const forksResult = await query(`
      SELECT
        a.asset_id,
        a.name,
        a.slug,
        u.username as owner,
        u.avatar_url as owner_avatar,
        a.description,
        a.license,
        a.download_count,
        a.latest_version,
        a.created_at,
        v.thumbnail_url
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE a.forked_from_id = $1 AND a.is_public = true
      ORDER BY a.created_at DESC
    `, [original.id]);

    res.json({
      forkCount: original.fork_count || 0,
      forks: forksResult.rows.map(f => ({
        id: f.asset_id,
        name: f.name,
        slug: `${f.owner}/${f.slug}`,
        owner: f.owner,
        ownerAvatar: f.owner_avatar,
        description: f.description,
        license: f.license,
        downloadCount: f.download_count,
        latestVersion: f.latest_version,
        thumbnailUrl: f.thumbnail_url,
        createdAt: f.created_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

export default router;
