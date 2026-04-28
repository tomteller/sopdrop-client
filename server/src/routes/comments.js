/**
 * Comments routes
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate, optionalAuth } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { sanitizeMarkdown } from '../middleware/security.js';

const router = Router();

/**
 * GET /comments/:slug
 * Get comments for an asset
 */
router.get('/:slug(*)', optionalAuth, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new NotFoundError('Asset not found');
    }

    const [owner, assetSlug] = parts;

    // Get asset
    const assetResult = await query(`
      SELECT a.id, a.is_public, a.owner_id FROM assets a
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

    // Get comments (top-level only, replies nested)
    const result = await query(`
      SELECT
        c.comment_id as id,
        c.content,
        c.is_edited,
        c.created_at,
        c.updated_at,
        c.parent_id,
        u.username,
        u.display_name,
        u.avatar_url
      FROM comments c
      JOIN users u ON c.user_id = u.id
      WHERE c.asset_id = $1
      ORDER BY c.created_at ASC
    `, [asset.id]);

    // Build nested structure
    const commentsMap = new Map();
    const topLevel = [];

    result.rows.forEach(c => {
      const comment = {
        id: c.id,
        content: c.content,
        isEdited: c.is_edited,
        createdAt: c.created_at,
        updatedAt: c.updated_at,
        author: {
          username: c.username,
          displayName: c.display_name,
          avatarUrl: c.avatar_url,
        },
        replies: [],
      };
      commentsMap.set(c.id, comment);

      if (c.parent_id) {
        const parent = commentsMap.get(c.parent_id);
        if (parent) {
          parent.replies.push(comment);
        }
      } else {
        topLevel.push(comment);
      }
    });

    res.json({ comments: topLevel });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /comments/:slug
 * Add a comment to an asset
 */
router.post('/:slug(*)', authenticate, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new NotFoundError('Asset not found');
    }

    const [owner, assetSlug] = parts;
    const { content: rawContent, parentId } = req.body;

    const content = sanitizeMarkdown(rawContent, 5000);

    if (!content || content.trim().length === 0) {
      throw new ValidationError('Comment content is required');
    }

    // Get asset
    const assetResult = await query(`
      SELECT a.id, a.is_public FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2 AND a.is_public = true
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const assetId = assetResult.rows[0].id;

    // Validate parent if provided
    let parentDbId = null;
    if (parentId) {
      const parentResult = await query(`
        SELECT id FROM comments WHERE comment_id = $1 AND asset_id = $2
      `, [parentId, assetId]);

      if (parentResult.rows.length === 0) {
        throw new NotFoundError('Parent comment not found');
      }
      parentDbId = parentResult.rows[0].id;
    }

    // Create comment
    const result = await query(`
      INSERT INTO comments (asset_id, user_id, parent_id, content)
      VALUES ($1, $2, $3, $4)
      RETURNING comment_id, content, created_at
    `, [assetId, req.user.id, parentDbId, content.trim()]);

    // Update comment count
    await query(`
      UPDATE assets SET comment_count = (
        SELECT COUNT(*) FROM comments WHERE asset_id = $1
      ) WHERE id = $1
    `, [assetId]);

    res.status(201).json({
      id: result.rows[0].comment_id,
      content: result.rows[0].content,
      createdAt: result.rows[0].created_at,
      author: {
        username: req.user.username,
        displayName: req.user.displayName,
        avatarUrl: req.user.avatarUrl,
      },
      replies: [],
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /comments/:commentId
 * Edit a comment
 */
router.put('/:commentId', authenticate, async (req, res, next) => {
  try {
    const { commentId } = req.params;
    const { content: rawContent } = req.body;

    const content = sanitizeMarkdown(rawContent, 5000);

    if (!content || content.trim().length === 0) {
      throw new ValidationError('Comment content is required');
    }

    // Get comment and verify ownership
    const commentResult = await query(`
      SELECT id, user_id FROM comments WHERE comment_id = $1
    `, [commentId]);

    if (commentResult.rows.length === 0) {
      throw new NotFoundError('Comment not found');
    }

    const comment = commentResult.rows[0];

    if (comment.user_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only edit your own comments');
    }

    // Update comment
    const result = await query(`
      UPDATE comments
      SET content = $1, is_edited = true, updated_at = NOW()
      WHERE id = $2
      RETURNING comment_id, content, is_edited, updated_at
    `, [content.trim(), comment.id]);

    res.json({
      id: result.rows[0].comment_id,
      content: result.rows[0].content,
      isEdited: result.rows[0].is_edited,
      updatedAt: result.rows[0].updated_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /comments/:commentId
 * Delete a comment
 */
router.delete('/:commentId', authenticate, async (req, res, next) => {
  try {
    const { commentId } = req.params;

    // Get comment and verify ownership
    const commentResult = await query(`
      SELECT c.id, c.user_id, c.asset_id, a.owner_id as asset_owner_id
      FROM comments c
      JOIN assets a ON c.asset_id = a.id
      WHERE c.comment_id = $1
    `, [commentId]);

    if (commentResult.rows.length === 0) {
      throw new NotFoundError('Comment not found');
    }

    const comment = commentResult.rows[0];

    // Allow deletion by comment author, asset owner, or admin
    const canDelete = comment.user_id === req.user.id ||
                     comment.asset_owner_id === req.user.id ||
                     req.user.isAdmin;

    if (!canDelete) {
      throw new ForbiddenError('You cannot delete this comment');
    }

    // Delete comment (cascade deletes replies)
    await query('DELETE FROM comments WHERE id = $1', [comment.id]);

    // Update comment count
    await query(`
      UPDATE assets SET comment_count = (
        SELECT COUNT(*) FROM comments WHERE asset_id = $1
      ) WHERE id = $1
    `, [comment.asset_id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

export default router;
