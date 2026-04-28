/**
 * User Folders routes - Personal asset organization
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { sanitizePlainText } from '../middleware/security.js';

const router = Router();

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
 * GET /folders
 * List current user's folders (hierarchical)
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const { flat } = req.query;

    const result = await query(`
      SELECT
        f.folder_id as id,
        f.name,
        f.slug,
        f.description,
        f.color,
        f.icon,
        f.asset_count,
        f.position,
        f.created_at,
        p.folder_id as parent_id,
        p.slug as parent_slug
      FROM user_folders f
      LEFT JOIN user_folders p ON f.parent_id = p.id
      WHERE f.user_id = $1
      ORDER BY f.parent_id NULLS FIRST, f.position ASC, f.name ASC
    `, [req.user.id]);

    if (flat === 'true') {
      res.json({
        folders: result.rows.map(f => ({
          id: f.id,
          name: f.name,
          slug: f.slug,
          description: f.description,
          color: f.color,
          icon: f.icon,
          assetCount: f.asset_count,
          parentId: f.parent_id,
          parentSlug: f.parent_slug,
        })),
      });
    } else {
      // Build hierarchical structure
      const foldersMap = new Map();
      const roots = [];

      result.rows.forEach(f => {
        const folder = {
          id: f.id,
          name: f.name,
          slug: f.slug,
          description: f.description,
          color: f.color,
          icon: f.icon,
          assetCount: f.asset_count,
          children: [],
        };
        foldersMap.set(f.id, folder);

        if (!f.parent_id) {
          roots.push(folder);
        }
      });

      // Attach children
      result.rows.forEach(f => {
        if (f.parent_id) {
          const parent = foldersMap.get(f.parent_id);
          const child = foldersMap.get(f.id);
          if (parent && child) {
            parent.children.push(child);
          }
        }
      });

      res.json({ folders: roots });
    }
  } catch (error) {
    next(error);
  }
});

/**
 * GET /folders/:slug
 * Get a single folder with its assets
 */
router.get('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { page = 1, limit = 20 } = req.query;
    const offset = (parseInt(page) - 1) * parseInt(limit);

    // Get folder
    const folderResult = await query(`
      SELECT
        f.id,
        f.folder_id,
        f.name,
        f.slug,
        f.description,
        f.color,
        f.icon,
        f.asset_count,
        p.folder_id as parent_id,
        p.slug as parent_slug,
        p.name as parent_name
      FROM user_folders f
      LEFT JOIN user_folders p ON f.parent_id = p.id
      WHERE f.user_id = $1 AND f.slug = $2
    `, [req.user.id, slug]);

    if (folderResult.rows.length === 0) {
      throw new NotFoundError('Folder not found');
    }

    const folder = folderResult.rows[0];

    // Get subfolders
    const subfoldersResult = await query(`
      SELECT
        folder_id as id,
        name,
        slug,
        color,
        icon,
        asset_count
      FROM user_folders
      WHERE user_id = $1 AND parent_id = $2
      ORDER BY position ASC, name ASC
    `, [req.user.id, folder.id]);

    // Get assets in folder
    const assetsResult = await query(`
      SELECT
        a.asset_id as id,
        a.name,
        a.slug,
        a.asset_type as type,
        a.houdini_context as context,
        a.description,
        a.tags,
        a.visibility,
        a.download_count,
        a.favorite_count,
        a.latest_version as version,
        a.created_at,
        a.updated_at,
        v.thumbnail_url
      FROM assets a
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE a.owner_id = $1 AND a.folder_id = $2
      ORDER BY a.updated_at DESC
      LIMIT $3 OFFSET $4
    `, [req.user.id, folder.id, parseInt(limit), offset]);

    res.json({
      folder: {
        id: folder.folder_id,
        name: folder.name,
        slug: folder.slug,
        description: folder.description,
        color: folder.color,
        icon: folder.icon,
        assetCount: folder.asset_count,
        parent: folder.parent_id ? {
          id: folder.parent_id,
          slug: folder.parent_slug,
          name: folder.parent_name,
        } : null,
      },
      subfolders: subfoldersResult.rows.map(f => ({
        id: f.id,
        name: f.name,
        slug: f.slug,
        color: f.color,
        icon: f.icon,
        assetCount: f.asset_count,
      })),
      assets: assetsResult.rows.map(a => ({
        id: a.id,
        name: a.name,
        slug: `${req.user.username}/${a.slug}`,
        type: a.type,
        context: a.context,
        description: a.description,
        tags: a.tags || [],
        visibility: a.visibility,
        downloadCount: a.download_count,
        favoriteCount: a.favorite_count,
        version: a.version,
        thumbnailUrl: a.thumbnail_url,
        createdAt: a.created_at,
        updatedAt: a.updated_at,
      })),
      pagination: {
        page: parseInt(page),
        limit: parseInt(limit),
        total: folder.asset_count,
        totalPages: Math.ceil(folder.asset_count / parseInt(limit)),
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /folders
 * Create a new folder
 */
router.post('/', authenticate, async (req, res, next) => {
  try {
    const { name: rawName, description: rawDescription, color, icon, parentSlug, position } = req.body;

    const name = sanitizePlainText(rawName, 100);
    const description = sanitizePlainText(rawDescription, 500);

    if (!name) {
      throw new ValidationError('Folder name is required');
    }

    const slug = slugify(name);

    // Check for duplicate slug
    const existingResult = await query(
      'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
      [req.user.id, slug]
    );

    if (existingResult.rows.length > 0) {
      throw new ValidationError('A folder with this name already exists');
    }

    // Get parent folder ID if provided
    let parentId = null;
    if (parentSlug) {
      const parentResult = await query(
        'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
        [req.user.id, parentSlug]
      );
      if (parentResult.rows.length === 0) {
        throw new NotFoundError('Parent folder not found');
      }
      parentId = parentResult.rows[0].id;
    }

    const result = await query(`
      INSERT INTO user_folders (user_id, name, slug, description, color, icon, parent_id, position)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      RETURNING folder_id as id, name, slug, description, color, icon
    `, [req.user.id, name, slug, description, color, icon, parentId, position || 0]);

    res.status(201).json({
      folder: {
        id: result.rows[0].id,
        name: result.rows[0].name,
        slug: result.rows[0].slug,
        description: result.rows[0].description,
        color: result.rows[0].color,
        icon: result.rows[0].icon,
        assetCount: 0,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /folders/:slug
 * Update a folder
 */
router.put('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { name: rawName, description: rawDescription, color, icon, position } = req.body;

    // Get folder
    const folderResult = await query(
      'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
      [req.user.id, slug]
    );

    if (folderResult.rows.length === 0) {
      throw new NotFoundError('Folder not found');
    }

    const folderId = folderResult.rows[0].id;

    const updates = [];
    const values = [];
    let paramIndex = 1;

    if (rawName !== undefined) {
      const name = sanitizePlainText(rawName, 100);
      updates.push(`name = $${paramIndex++}`);
      values.push(name);
      // Update slug too
      updates.push(`slug = $${paramIndex++}`);
      values.push(slugify(name));
    }

    if (rawDescription !== undefined) {
      updates.push(`description = $${paramIndex++}`);
      values.push(sanitizePlainText(rawDescription, 500));
    }

    if (color !== undefined) {
      updates.push(`color = $${paramIndex++}`);
      values.push(color);
    }

    if (icon !== undefined) {
      updates.push(`icon = $${paramIndex++}`);
      values.push(icon);
    }

    if (position !== undefined) {
      updates.push(`position = $${paramIndex++}`);
      values.push(position);
    }

    if (updates.length === 0) {
      throw new ValidationError('No fields to update');
    }

    updates.push('updated_at = NOW()');

    const result = await query(`
      UPDATE user_folders
      SET ${updates.join(', ')}
      WHERE id = $${paramIndex}
      RETURNING folder_id as id, name, slug, description, color, icon
    `, [...values, folderId]);

    res.json({
      folder: {
        id: result.rows[0].id,
        name: result.rows[0].name,
        slug: result.rows[0].slug,
        description: result.rows[0].description,
        color: result.rows[0].color,
        icon: result.rows[0].icon,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /folders/:slug
 * Delete a folder (assets are moved to no folder, not deleted)
 */
router.delete('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;

    // Get folder
    const folderResult = await query(
      'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
      [req.user.id, slug]
    );

    if (folderResult.rows.length === 0) {
      throw new NotFoundError('Folder not found');
    }

    const folderId = folderResult.rows[0].id;

    // Move assets out of folder
    await query('UPDATE assets SET folder_id = NULL WHERE folder_id = $1', [folderId]);

    // Move subfolders to parent (or root)
    await query(`
      UPDATE user_folders
      SET parent_id = (SELECT parent_id FROM user_folders WHERE id = $1)
      WHERE parent_id = $1
    `, [folderId]);

    // Delete folder
    await query('DELETE FROM user_folders WHERE id = $1', [folderId]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /folders/:slug/assets
 * Move assets to a folder
 */
router.put('/:slug/assets', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { assetSlugs } = req.body;

    if (!Array.isArray(assetSlugs) || assetSlugs.length === 0) {
      throw new ValidationError('assetSlugs must be a non-empty array');
    }

    // Get folder (or null for "no folder")
    let folderId = null;
    if (slug !== 'none') {
      const folderResult = await query(
        'SELECT id FROM user_folders WHERE user_id = $1 AND slug = $2',
        [req.user.id, slug]
      );
      if (folderResult.rows.length === 0) {
        throw new NotFoundError('Folder not found');
      }
      folderId = folderResult.rows[0].id;
    }

    // Move assets
    const result = await query(`
      UPDATE assets
      SET folder_id = $1, updated_at = NOW()
      WHERE owner_id = $2 AND slug = ANY($3)
      RETURNING slug
    `, [folderId, req.user.id, assetSlugs]);

    res.json({
      success: true,
      movedCount: result.rowCount,
    });
  } catch (error) {
    next(error);
  }
});

export default router;
