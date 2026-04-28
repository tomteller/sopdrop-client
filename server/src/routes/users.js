/**
 * User routes - Public profiles and user assets
 */

import { Router } from 'express';
import multer from 'multer';
import path from 'path';
import crypto from 'crypto';
import { query } from '../models/db.js';
import { authenticate, optionalAuth } from '../middleware/auth.js';
import { NotFoundError, ValidationError } from '../middleware/errorHandler.js';
import { sanitizePlainText, sanitizeMarkdown } from '../middleware/security.js';
import storage from '../services/storage.js';
import { validateUploads } from '../services/fileValidation.js';

const router = Router();

// Avatar upload configuration (memoryStorage: files available as req.file.buffer)
const uploadAvatar = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 5 * 1024 * 1024, // 5MB max for avatars
  },
  fileFilter: (req, file, cb) => {
    const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (allowedTypes.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new Error('Only JPEG, PNG, GIF, and WebP images are allowed'));
    }
  },
});

/**
 * GET /users/me/assets
 * Get current user's own assets (for version up workflow)
 */
router.get('/me/assets', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT
        a.asset_id,
        a.name,
        a.slug,
        a.asset_type,
        a.houdini_context,
        a.description,
        a.tags,
        a.latest_version,
        a.download_count,
        a.is_public,
        a.created_at,
        v.thumbnail_url
      FROM assets a
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE a.owner_id = $1
      ORDER BY a.name ASC
    `, [req.user.id]);

    res.json({
      assets: result.rows.map(a => ({
        asset_id: a.asset_id,
        name: a.name,
        slug: `${req.user.username}/${a.slug}`,
        assetType: a.asset_type,
        houdiniContext: a.houdini_context,
        description: a.description,
        tags: a.tags || [],
        latestVersion: a.latest_version,
        downloadCount: a.download_count,
        isPublic: a.is_public,
        thumbnailUrl: a.thumbnail_url,
        createdAt: a.created_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /users/:username
 * Get public user profile
 */
router.get('/:username', async (req, res, next) => {
  try {
    const { username } = req.params;

    const result = await query(`
      SELECT
        u.id, u.username, u.display_name, u.avatar_url, u.bio, u.website,
        u.company, u.job_title, u.location, u.social_links,
        u.is_verified, u.download_count, u.created_at,
        COUNT(a.id) FILTER (WHERE a.is_public = true AND a.is_deprecated = false) AS asset_count
      FROM users u
      LEFT JOIN assets a ON a.owner_id = u.id
      WHERE u.username = $1
      GROUP BY u.id
    `, [username.toLowerCase()]);

    if (result.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const user = result.rows[0];

    res.json({
      username: user.username,
      displayName: user.display_name,
      avatarUrl: user.avatar_url,
      bio: user.bio,
      website: user.website,
      company: user.company,
      jobTitle: user.job_title,
      location: user.location,
      socialLinks: user.social_links || {},
      isVerified: user.is_verified,
      assetCount: parseInt(user.asset_count) || 0,
      downloadCount: user.download_count,
      joinedAt: user.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /users/:username/assets
 * Get user's public assets
 */
router.get('/:username/assets', optionalAuth, async (req, res, next) => {
  try {
    const { username } = req.params;
    const { sort = 'recent', limit = 50, offset = 0 } = req.query;

    // Get user
    const userResult = await query(`
      SELECT id FROM users WHERE username = $1
    `, [username.toLowerCase()]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const userId = userResult.rows[0].id;

    // Build visibility clause — always exclude soft-deleted assets
    let visibilityClause = 'AND a.is_public = true AND a.is_deprecated = false';

    // If viewing own profile, show all non-deleted assets (including private)
    if (req.user && req.user.id === userId) {
      visibilityClause = 'AND a.is_deprecated = false';
    }

    // Sort order
    let orderBy = 'ORDER BY a.created_at DESC';
    if (sort === 'downloads') {
      orderBy = 'ORDER BY a.download_count DESC';
    } else if (sort === 'name') {
      orderBy = 'ORDER BY a.name ASC';
    }

    const limitVal = Math.min(parseInt(limit) || 50, 100);
    const offsetVal = parseInt(offset) || 0;

    const result = await query(`
      SELECT
        a.asset_id,
        a.name,
        a.slug,
        a.asset_type,
        a.houdini_context,
        a.description,
        a.license,
        a.tags,
        a.latest_version,
        a.download_count,
        a.is_public,
        a.is_deprecated,
        a.created_at,
        v.thumbnail_url
      FROM assets a
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE a.owner_id = $1 ${visibilityClause}
      ${orderBy}
      LIMIT $2 OFFSET $3
    `, [userId, limitVal, offsetVal]);

    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM assets a
      WHERE a.owner_id = $1 ${visibilityClause}
    `, [userId]);

    res.json({
      assets: result.rows.map(a => ({
        id: a.asset_id,
        name: a.name,
        slug: `${username}/${a.slug}`,
        type: a.asset_type,
        context: a.houdini_context,
        description: a.description,
        license: a.license,
        tags: a.tags || [],
        latestVersion: a.latest_version,
        downloadCount: a.download_count,
        isPublic: a.is_public,
        isDeprecated: a.is_deprecated,
        thumbnailUrl: a.thumbnail_url,
        createdAt: a.created_at,
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
 * POST /users/:username/avatar
 * Upload user avatar
 */
router.post('/:username/avatar', authenticate, uploadAvatar.single('avatar'), validateUploads(), async (req, res, next) => {
  let avatarKey = null;
  try {
    const { username } = req.params;

    // Verify it's the user's own profile
    if (req.user.username.toLowerCase() !== username.toLowerCase() && !req.user.isAdmin) {
      throw new NotFoundError('User not found');
    }

    if (!req.file) {
      throw new ValidationError('No avatar file provided');
    }

    // Get old avatar to delete
    const oldResult = await query('SELECT avatar_url FROM users WHERE id = $1', [req.user.id]);
    const oldAvatarUrl = oldResult.rows[0]?.avatar_url;

    // Generate filename and upload to storage
    const ext = path.extname(req.file.originalname).toLowerCase() || '.jpg';
    const filename = `${req.user.id}-${crypto.randomBytes(8).toString('hex')}${ext}`;
    avatarKey = `avatars/${filename}`;
    await storage.upload(avatarKey, req.file.buffer, req.file.mimetype);

    // Get public URL for the avatar
    const avatarUrl = storage.getPublicUrl(avatarKey);

    // Update user with new avatar
    await query('UPDATE users SET avatar_url = $1, updated_at = NOW() WHERE id = $2', [avatarUrl, req.user.id]);

    // Delete old avatar file if it exists
    if (oldAvatarUrl) {
      const oldFilename = oldAvatarUrl.split('/').pop();
      if (oldFilename && /^[a-zA-Z0-9_-]+\.[a-z]+$/.test(oldFilename)) {
        await storage.remove(`avatars/${oldFilename}`);
      }
    }

    res.json({ avatarUrl });
  } catch (error) {
    // Clean up uploaded file on error
    if (avatarKey) {
      await storage.remove(avatarKey);
    }
    next(error);
  }
});

/**
 * DELETE /users/:username/avatar
 * Remove user avatar
 */
router.delete('/:username/avatar', authenticate, async (req, res, next) => {
  try {
    const { username } = req.params;

    // Verify it's the user's own profile
    if (req.user.username.toLowerCase() !== username.toLowerCase() && !req.user.isAdmin) {
      throw new NotFoundError('User not found');
    }

    // Get current avatar
    const result = await query('SELECT avatar_url FROM users WHERE id = $1', [req.user.id]);
    const avatarUrl = result.rows[0]?.avatar_url;

    // Remove avatar from database
    await query('UPDATE users SET avatar_url = NULL, updated_at = NOW() WHERE id = $1', [req.user.id]);

    // Delete file from storage
    if (avatarUrl) {
      const filename = avatarUrl.split('/').pop();
      if (filename && /^[a-zA-Z0-9_-]+\.[a-z]+$/.test(filename)) {
        await storage.remove(`avatars/${filename}`);
      }
    }

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /users/:username
 * Update user profile (own profile only)
 */
router.put('/:username', authenticate, async (req, res, next) => {
  try {
    const { username } = req.params;

    // Verify it's the user's own profile
    if (req.user.username.toLowerCase() !== username.toLowerCase() && !req.user.isAdmin) {
      throw new NotFoundError('User not found');
    }

    const {
      displayName: rawDisplayName,
      bio: rawBio,
      website: rawWebsite,
      company: rawCompany,
      jobTitle: rawJobTitle,
      location: rawLocation,
      socialLinks: rawSocialLinks,
    } = req.body;

    const updates = [];
    const values = [];
    let paramIndex = 1;

    if (rawDisplayName !== undefined) {
      const displayName = sanitizePlainText(rawDisplayName, 100);
      updates.push(`display_name = $${paramIndex++}`);
      values.push(displayName || null);
    }

    if (rawBio !== undefined) {
      const bio = sanitizeMarkdown(rawBio, 500);
      updates.push(`bio = $${paramIndex++}`);
      values.push(bio || null);
    }

    if (rawWebsite !== undefined) {
      // Sanitize and validate website URL
      const website = sanitizePlainText(rawWebsite, 255);
      if (website && !/^https?:\/\/.+/.test(website)) {
        throw new ValidationError('Website must be a valid URL starting with http:// or https://');
      }
      updates.push(`website = $${paramIndex++}`);
      values.push(website || null);
    }

    if (rawCompany !== undefined) {
      const company = sanitizePlainText(rawCompany, 100);
      updates.push(`company = $${paramIndex++}`);
      values.push(company || null);
    }

    if (rawJobTitle !== undefined) {
      const jobTitle = sanitizePlainText(rawJobTitle, 100);
      updates.push(`job_title = $${paramIndex++}`);
      values.push(jobTitle || null);
    }

    if (rawLocation !== undefined) {
      const location = sanitizePlainText(rawLocation, 100);
      updates.push(`location = $${paramIndex++}`);
      values.push(location || null);
    }

    if (rawSocialLinks !== undefined) {
      // Validate social links structure
      const validPlatforms = [
        'twitter', 'linkedin', 'github', 'artstation',
        'instagram', 'youtube', 'vimeo', 'imdb', 'twitch', 'custom'
      ];

      if (typeof rawSocialLinks !== 'object' || Array.isArray(rawSocialLinks)) {
        throw new ValidationError('socialLinks must be an object');
      }

      const sanitizedLinks = {};
      for (const [platform, value] of Object.entries(rawSocialLinks)) {
        if (!validPlatforms.includes(platform)) {
          throw new ValidationError(`Invalid social platform: ${platform}. Valid platforms: ${validPlatforms.join(', ')}`);
        }

        if (value === null || value === '') {
          // Allow clearing a link
          continue;
        }

        if (typeof value !== 'string') {
          throw new ValidationError(`Social link value for ${platform} must be a string`);
        }

        // Sanitize and validate the value
        const sanitizedValue = sanitizePlainText(value, 255);

        // For URL-based platforms, validate URL format and domain
        const urlPlatforms = ['linkedin', 'artstation', 'youtube', 'vimeo', 'imdb', 'custom'];
        if (urlPlatforms.includes(platform) && sanitizedValue) {
          if (!/^https?:\/\/.+/.test(sanitizedValue)) {
            throw new ValidationError(`${platform} must be a valid URL starting with http:// or https://`);
          }

          // Validate domain matches expected service (skip for 'custom')
          if (platform !== 'custom') {
            const expectedDomains = {
              linkedin: ['linkedin.com', 'www.linkedin.com'],
              artstation: ['artstation.com', 'www.artstation.com'],
              youtube: ['youtube.com', 'www.youtube.com', 'youtu.be'],
              vimeo: ['vimeo.com', 'www.vimeo.com'],
              imdb: ['imdb.com', 'www.imdb.com'],
            };
            try {
              const urlHost = new URL(sanitizedValue).hostname.toLowerCase();
              if (!expectedDomains[platform]?.some(d => urlHost === d || urlHost.endsWith('.' + d))) {
                throw new ValidationError(`${platform} URL must be on ${expectedDomains[platform][0]}`);
              }
            } catch (urlErr) {
              if (urlErr instanceof ValidationError) throw urlErr;
              throw new ValidationError(`${platform} must be a valid URL`);
            }
          }
        }

        // For handle-based platforms, strip @ if present and validate
        const handlePlatforms = ['twitter', 'github', 'instagram', 'twitch'];
        if (handlePlatforms.includes(platform) && sanitizedValue) {
          const handle = sanitizedValue.replace(/^@/, '');
          if (!/^[a-zA-Z0-9_.-]+$/.test(handle)) {
            throw new ValidationError(`${platform} handle contains invalid characters`);
          }
          sanitizedLinks[platform] = handle;
          continue;
        }

        sanitizedLinks[platform] = sanitizedValue;
      }

      updates.push(`social_links = $${paramIndex++}`);
      values.push(JSON.stringify(sanitizedLinks));
    }

    if (updates.length === 0) {
      throw new ValidationError('No fields to update');
    }

    updates.push(`updated_at = NOW()`);

    const result = await query(`
      UPDATE users
      SET ${updates.join(', ')}
      WHERE username = $${paramIndex}
      RETURNING username, display_name, bio, website, company, job_title, location, social_links, updated_at
    `, [...values, username.toLowerCase()]);

    if (result.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const updated = result.rows[0];
    res.json({
      username: updated.username,
      displayName: updated.display_name,
      bio: updated.bio,
      website: updated.website,
      company: updated.company,
      jobTitle: updated.job_title,
      location: updated.location,
      socialLinks: updated.social_links || {},
      updatedAt: updated.updated_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /users/me/export/assets
 * Export all user's own assets with full code (for data portability)
 */
router.get('/me/export/assets', authenticate, async (req, res, next) => {
  try {
    // Get all user's assets with their latest version files
    const assetsResult = await query(`
      SELECT
        a.asset_id,
        a.name,
        a.slug,
        a.asset_type,
        a.houdini_context,
        a.description,
        a.readme,
        a.license,
        a.license_url,
        a.tags,
        a.metadata,
        a.created_at,
        a.updated_at,
        v.version,
        v.file_path,
        v.changelog,
        v.node_count,
        v.node_names,
        v.published_at
      FROM assets a
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE a.owner_id = $1
      ORDER BY a.name ASC
    `, [req.user.id]);

    const exportData = {
      format: 'sopdrop-export-v1',
      exportedAt: new Date().toISOString(),
      exportedBy: req.user.username,
      assetCount: assetsResult.rows.length,
      assets: [],
    };

    for (const asset of assetsResult.rows) {
      let code = null;

      // Read the actual file content if it exists
      if (asset.file_path) {
        try {
          const key = storage.pathToKey(asset.file_path);
          const buf = await storage.download(key);
          const parsed = JSON.parse(buf.toString('utf-8'));
          code = parsed.code || null;
        } catch (err) {
          // File might not exist or be readable, continue without code
          console.warn(`Could not read file for asset ${asset.asset_id}:`, err.message);
        }
      }

      exportData.assets.push({
        assetId: asset.asset_id,
        name: asset.name,
        slug: asset.slug,
        type: asset.asset_type,
        context: asset.houdini_context,
        description: asset.description,
        readme: asset.readme,
        license: asset.license,
        licenseUrl: asset.license_url,
        tags: asset.tags || [],
        metadata: asset.metadata || {},
        version: asset.version,
        changelog: asset.changelog,
        nodeCount: asset.node_count,
        nodeNames: asset.node_names || [],
        createdAt: asset.created_at,
        updatedAt: asset.updated_at,
        publishedAt: asset.published_at,
        code: code,
      });
    }

    res.json(exportData);
  } catch (error) {
    next(error);
  }
});

/**
 * GET /users/me/export/library
 * Export user's saved/favorited assets with full code (for data portability)
 */
router.get('/me/export/library', authenticate, async (req, res, next) => {
  try {
    // Get all user's saved assets with their files
    const savedResult = await query(`
      SELECT
        a.asset_id,
        a.name,
        a.slug,
        a.asset_type,
        a.houdini_context,
        a.description,
        a.license,
        a.tags,
        u.username as owner_username,
        v.version,
        v.file_path,
        v.node_count,
        v.node_names,
        s.created_at as saved_at,
        s.folder
      FROM saved_assets s
      JOIN assets a ON s.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE s.user_id = $1
      ORDER BY s.created_at DESC
    `, [req.user.id]);

    const exportData = {
      format: 'sopdrop-library-export-v1',
      exportedAt: new Date().toISOString(),
      exportedBy: req.user.username,
      assetCount: savedResult.rows.length,
      library: [],
    };

    for (const asset of savedResult.rows) {
      let code = null;

      // Read the actual file content if it exists
      if (asset.file_path) {
        try {
          const key = storage.pathToKey(asset.file_path);
          const buf = await storage.download(key);
          const parsed = JSON.parse(buf.toString('utf-8'));
          code = parsed.code || null;
        } catch (err) {
          // File might not exist or be readable, continue without code
          console.warn(`Could not read file for asset ${asset.asset_id}:`, err.message);
        }
      }

      exportData.library.push({
        assetId: asset.asset_id,
        name: asset.name,
        slug: `${asset.owner_username}/${asset.slug}`,
        owner: asset.owner_username,
        type: asset.asset_type,
        context: asset.houdini_context,
        description: asset.description,
        license: asset.license,
        tags: asset.tags || [],
        version: asset.version,
        nodeCount: asset.node_count,
        nodeNames: asset.node_names || [],
        folder: asset.folder,
        savedAt: asset.saved_at,
        code: code,
      });
    }

    res.json(exportData);
  } catch (error) {
    next(error);
  }
});

export default router;
