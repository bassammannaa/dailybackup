# -*- coding: utf-8 -*-

from odoo import models, fields, api, tools, _
from odoo.exceptions import Warning
import odoo
from odoo.http import content_disposition
from odoo.exceptions import ValidationError

import logging
logger = logging.getLogger(__name__)
from ftplib import FTP
import os
import datetime

try:
    from xmlrpc import client as xmlrpclib
except ImportError:
    import xmlrpclib
import time
import base64
import socket

try:
    import paramiko
except ImportError:
    raise ImportError(
        'This module needs paramiko to automatically write backups to the FTP through SFTP. Please install paramiko on your system. (sudo pip3 install paramiko)')


def execute(connector, method, *args):
    res = False
    try:
        res = getattr(connector, method)(*args)
    except socket.error as error:
        logger.critical('Error while executing the method "execute". Error: ' + str(error))
        raise error
    return res


class BackupProcess(models.Model):
    _name = 'dailybackup.backupprocess'

    @api.multi
    def get_db_list(self, host, port, context={}):
        try:
            uri = 'http://' + host + ':' + port
            conn = xmlrpclib.ServerProxy(uri + '/xmlrpc/db')
            db_list = execute(conn, 'list')
            logger.info('Function: get_db_list - Parameters: uri: %s - conn: %s' % (uri, conn))
            return db_list

        except Exception as e:
            logger.exception("get_db_list Method")
            raise ValidationError(e)

    @api.multi
    def _get_db_name(self):
        try:
            db_name = self._cr.dbname
            logger.info('Function: _get_db_name - Parameters: db_name: %s' % db_name)
            return db_name
        except Exception as e:
            logger.exception("_get_db_name Method")
            raise ValidationError(e)



    # Columns for local server configuration
    host = fields.Char('Host', )
    port = fields.Char('Port', )
    name = fields.Char('Database', help='Database you want to schedule backups for',
                       default=_get_db_name)
    folder = fields.Char('Backup Directory', help='Absolute path for storing the backups', default='db_backup')
    backup_type = fields.Selection([('zip', 'Zip'), ('dump', 'Dump')], 'Backup Type', default='zip')
    autoremove = fields.Boolean('Auto. Remove Backups',
                                help='If you check this option you can choose to automaticly remove the backup after xx days')
    days_to_keep = fields.Integer('Remove after x days',
                                  help="Choose after how many days the backup should be deleted. For example:\nIf you fill in "
                                       "5 the backups will be removed after 5 days.", )

    # Columns for external server (SFTP)
    sftp_write = fields.Boolean('Write to external server with sftp',
                                help="If you check this option you can specify the details needed to write to a remote server with SFTP.")
    sftp_path = fields.Char('Path external server',
                            help='The location to the folder where the dumps should be written to. '
                                 'For example /odoo/backups/.\nFiles will then be written to /odoo/backups/ on your remote server.')
    sftp_host = fields.Char('IP Address SFTP Server',
                            help='The IP address from your remote server. For example 192.168.0.1')
    sftp_port = fields.Integer('SFTP Port', help='The port on the FTP server that accepts SSH/SFTP calls.', default=22)
    sftp_user = fields.Char('Username SFTP Server',
                            help='The username where the SFTP connection should be made with. '
                                 'This is the user on the external server.')
    sftp_password = fields.Char('Password User SFTP Server',
                                help='The password from the user where the SFTP connection should be made with. This is '
                                     'the password from the user on the external server.')
    days_to_keep_sftp = fields.Integer('Remove SFTP after x days',
                                       help='Choose after how many days the backup should be deleted from the FTP server. '
                                            'For example:\nIf you fill in 5 the backups will be removed after 5 days from the FTP server.',
                                       default=30)
    send_mail_sftp_fail = fields.Boolean('Auto. E-mail on backup fail',
                                         help='If you check this option you can choose to automaticly get '
                                              'e-mailed when the backup to the external server failed.')
    email_to_notify = fields.Char('E-mail to notify',
                                  help='Fill in the e-mail where you want to be notified that '
                                       'the backup failed on the FTP.')

    @api.multi
    def _check_db_exist(self):
        try:
            self.ensure_one()
            db_list = self.get_db_list(self.host, self.port)
            if self.name in db_list:
                logger.info('Function: _check_db_exist - Parameters: db_name: %s' % self.name)
                return True
            return False
        except Exception as e:
            logger.exception("_check_db_exist Method")
            raise ValidationError(e)

    _constraints = [(_check_db_exist, _('Error ! No such database exists!'), [])]

    @api.multi
    def test_sftp_connection(self, context=None):
        self.ensure_one()

        # Check if there is a success or fail and write messages
        message_title = ""
        message_content = ""
        error = ""
        has_failed = False

        for rec in self:
            db_list = self.get_db_list(rec.host, rec.port)
            path_to_write_to = rec.sftp_path
            ip_host = rec.sftp_host
            port_host = rec.sftp_port
            user_name_login = rec.sftp_user
            password_login = rec.sftp_password

            logger.info('Function: test_sftp_connection - Parameters: path_to_write_to: '
                        '%s - ip_host: %s - port_host: %s - user_name_login: %s - '
                        'password_login: %s' % (path_to_write_to, ip_host, port_host, user_name_login, password_login))

            # Connect with external server over SFTP, so we know sure that everything works.
            s = None
            try:
                s = paramiko.SSHClient()
                s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                s.connect(ip_host, port_host, user_name_login, password_login, timeout=10)
                sftp = s.open_sftp()
                message_title = _("Function: test_sftp_connection  >> Connection Test Succeeded!\nEverything "
                                  "seems properly set up for FTP back-ups!")
            except Exception as e:
                logger.critical('Function: test_sftp_connection  >> '
                                'There was a problem connecting to the remote ftp: ' + str(e))
                error += str(e)
                has_failed = True
                message_title = _("Function: test_sftp_connection  >> Connection Test Failed!")
                if len(rec.sftp_host) < 8:
                    message_content += "\nYour IP address seems to be too short.\n"
                message_content += _("Here is what we got instead:\n")
            finally:
                if s:
                    s.close()

        if has_failed:
            raise Warning(message_title + '\n\n' + message_content + "%s" % str(error))
        else:
            raise Warning(message_title + '\n\n' + message_content)

    @api.model
    def schedule_backup_process(self):
        conf_ids = self.search([])
        folder_path = ''

        for rec in conf_ids:
            db_list = self.get_db_list(rec.host, rec.port)

            if rec.name in db_list:
                try:
                    logger.info(
                        "Function: schedule_backup_process - Folder path is " + str(rec.folder))
                    folder_path = rec.folder if rec.folder else '//db_backup'
                    logger.info(
                        "Function: schedule_backup_process - Folder path is " + str(folder_path))

                    if not os.path.isdir(folder_path):
                        logger.info(
                            "Function: schedule_backup_process - Folder is "
                            "not directory, we will create the directory")
                        os.makedirs(folder_path)
                    else:
                        logger.info(
                            "Function: schedule_backup_process - Folder is directory")
                except Exception as e:
                    raise ValidationError('Function: schedule_backup_process - error is ' + str(e))
                

            # Create name for dumpfile.
                bkp_file = '%s_%s.%s' % (time.strftime('%Y_%m_%d_%H_%M_%S'), rec.name, rec.backup_type)
                file_path = os.path.join(folder_path, bkp_file)
                uri = 'http://' + rec.host + ':' + rec.port
                conn = xmlrpclib.ServerProxy(uri + '/xmlrpc/db')
                bkp = ''

                logger.info('Function: schedule_backup_process - Parameters: bkp_file: '
                            '%s - file_path: %s - uri: %s - conn: %s - '
                            'bkp: %s' % (
                            bkp_file, file_path, uri, conn, bkp))

                try:
                    # try to backup database and write it away
                    fp = open(file_path, 'wb')
                    odoo.service.db.dump_db(rec.name, fp, rec.backup_type)
                    fp.close()
                except Exception as error:
                    logger.debug(
                        "Couldn't backup database %s. Bad database administrator "
                        "password for server running at http://%s:%s" % (
                        rec.name, rec.host, rec.port))
                    logger.info(
                        "Function: schedule_backup_process - Parameters: Couldn't backup database %s. "
                        "Bad database administrator password for server running at http://%s:%s" % (
                            rec.name, rec.host, rec.port))
                    logger.debug("Exact error from the exception: " + str(error))
                    logger.info("Function: schedule_backup_process - Parameters: Exact error from the exception: " + str(error))

                    continue

            else:
                logger.debug("database %s doesn't exist on http://%s:%s" % (rec.name, rec.host, rec.port))
                logger.info("Function: schedule_backup_process - database %s "
                            "doesn't exist on http://%s:%s" % (rec.name, rec.host, rec.port))

            # Check if user wants to write to SFTP or not.
            if rec.sftp_write is True:
                try:
                    # Store all values in variables
                    sftp = None
                    dir = folder_path
                    path_to_write_to = rec.sftp_path
                    ip_host = rec.sftp_host
                    port_host = rec.sftp_port
                    user_name_login = rec.sftp_user
                    password_login = rec.sftp_password

                    logger.debug('sftp remote path: %s' % path_to_write_to)

                    logger.info('Function: schedule_backup_process - Parameters: path_to_write_to: '
                                '%s - ip_host: %s - port_host: %s - user_name_login: %s - '
                                'password_login: %s' % (
                                path_to_write_to, ip_host, port_host, user_name_login, password_login))

                    try:
                        s = paramiko.SSHClient()
                        s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        s.connect(ip_host, port_host, user_name_login, password_login, timeout=20)
                        sftp = s.open_sftp()
                    except Exception as error:
                        logger.critical('Error connecting to remote server! Error: ' + str(error))
                        logger.info('Function: schedule_backup_process - Error connecting to remote'
                                    ' server! Error: ' + str(error))

                    try:
                        sftp.chdir(path_to_write_to)
                        logger.info('Function: schedule_backup_process - Parameters: '
                                    'path_to_write_to: %s' % path_to_write_to)

                    except IOError:
                        # Create directory and subdirs if they do not exist.
                        current_dir = ''
                        for dirElement in path_to_write_to.split('/'):
                            current_dir += dirElement + '/'
                            logger.info('Function: schedule_backup_process - Parameters: '
                                        'current_dir: %s' % current_dir)
                            try:
                                sftp.chdir(current_dir)
                            except:
                                logger.info('(Part of the) path didn\'t exist. Creating it now at ' + current_dir)

                                # Make directory and then navigate into it
                                sftp.mkdir(current_dir, 777)
                                sftp.chdir(current_dir)
                                pass
                    sftp.chdir(path_to_write_to)

                    # Loop over all files in the directory.
                    for f in os.listdir(dir):
                        if rec.name in f:
                            fullpath = os.path.join(dir, f)
                            logger.info('Function: schedule_backup_process - Parameters: '
                                        'fullpath: %s' % fullpath)
                            if os.path.isfile(fullpath):
                                try:
                                    sftp.stat(os.path.join(path_to_write_to, f))
                                    logger.debug(
                                        'File %s already exists on the remote FTP Server ------ skipped' % fullpath)
                                # This means the file does not exist (remote) yet!
                                except IOError:
                                    try:
                                        # sftp.put(fullpath, path_to_write_to)
                                        sftp.put(fullpath, os.path.join(path_to_write_to, f))
                                        logger.info('Copying File % s------ success' % fullpath)
                                    except Exception as err:
                                        logger.critical(
                                            'We couldn\'t write the file to the remote server. Error: ' + str(err))
                                        logger.info('Copying File % s------ failed' % fullpath)


                    # Navigate in to the correct folder.
                    sftp.chdir(path_to_write_to)

                    # Loop over all files in the directory from the back-ups.
                    # We will check the creation date of every back-up.
                    for file in sftp.listdir(path_to_write_to):
                        if rec.name in file:
                            # Get the full path
                            fullpath = os.path.join(path_to_write_to, file)
                            # Get the timestamp from the file on the external server
                            timestamp = sftp.stat(fullpath).st_atime
                            createtime = datetime.datetime.fromtimestamp(timestamp)
                            now = datetime.datetime.now()
                            delta = now - createtime
                            # If the file is older than the days_to_keep_sftp (the days to keep that the user filled in on the Odoo form it will be removed.
                            if delta.days >= rec.days_to_keep_sftp:
                                # Only delete files, no directories!
                                if sftp.isfile(fullpath) and (".dump" in file or '.zip' in file):
                                    logger.info("Delete too old file from SFTP servers: " + file)
                                    sftp.unlink(file)
                    # Close the SFTP session.
                    sftp.close()
                except Exception as e:
                    logger.debug('Exception! We couldn\'t back up to the FTP server..')
                    # At this point the SFTP backup failed. We will now check if the user wants
                    # an e-mail notification about this.
                    if rec.send_mail_sftp_fail:
                        try:
                            ir_mail_server = self.env['ir.mail_server']
                            message = "Dear,\n\nThe backup for the server " + rec.host + " (IP: " + rec.sftp_host + ") failed.Please check the following details:\n\nIP address SFTP server: " + rec.sftp_host + "\nUsername: " + rec.sftp_user + "\nPassword: " + rec.sftp_password + "\n\nError details: " + tools.ustr(
                                e) + "\n\nWith kind regards"
                            msg = ir_mail_server.build_email("auto_backup@" + rec.name + ".com", [rec.email_to_notify],
                                                             "Backup from " + rec.host + "(" + rec.sftp_host + ") failed",
                                                             message)
                            ir_mail_server.send_email(self._cr, self._uid, msg)
                        except Exception:
                            pass

            """
            Remove all old files (on local server) in case this is configured..
            """
            if rec.autoremove:
                dir = folder_path
                # Loop over all files in the directory.
                for f in os.listdir(dir):
                    fullpath = os.path.join(dir, f)
                    # Only delete the ones wich are from the current database
                    # (Makes it possible to save different databases in the same folder)
                    if rec.name in fullpath:
                        timestamp = os.stat(fullpath).st_ctime
                        createtime = datetime.datetime.fromtimestamp(timestamp)
                        now = datetime.datetime.now()
                        delta = now - createtime
                        if delta.days >= rec.days_to_keep:
                            # Only delete files (which are .dump and .zip), no directories.
                            if os.path.isfile(fullpath) and (".dump" in f or '.zip' in f):
                                logger.info("Delete local out-of-date file: " + fullpath)
                                os.remove(fullpath)

