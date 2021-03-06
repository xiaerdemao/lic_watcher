import re
import os
from datetime import date, time, datetime
from .lic_validator import home_page_schema, srv_features_schema
from functions.DB import DB_PATH, dict_factory
import sqlite3


class log_parser():
    # 分析lmstat -a -c 生成的log 文件

    # re 表达式
    _lmstat_time_pattern = r'^Flexible License Manager status on (?P<mday>\S+)\s(?P<mon>\d+)/(?P<day>\d+)/(?P<year>\d+)\s(?P<hour>\d+):(?P<min>\d+)'
    _lmstat_server_status = r'(?P<server>\S+): license server (?P<status>\S+)\s\(\w+\)\s(?P<version>\S+)$'
    _lmstat_feature_pattern = r'^ of (?P<feature>\S+):\s{2,10}\(Total of (?P<total_lic>\d+).{25,28}(?P<in_use_lic>\d+)'
    _lmstat_current_users_1 = r'\s{0,10}(?P<User>\S+)\s(?P<client>\S+)\s(?P<ip_addr>\S+).+start\s\S{3}\s(?P<date>\S+)\s(?P<time>\S+),\s(?P<lic_checkedout>\S+) licenses'
    _lmstat_current_users_2 = r'\s{0,10}(?P<User>\S+)\s(?P<client>\S+)\s(?P<ip_addr>\S+).+start\s\S{3}\s(?P<date>\S+)\s(?P<time>\S+)'

    def __init__(self, log_file, keyword):
        self.file = log_file
        self.keyword = keyword
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = dict_factory
        self.session = self.conn.cursor()

    def server_info_parser(self):
        # 根据log 文件名生成对应的server 信息
        current_port, current_srv = self.file.split('/')[-1].split('@')
        current_srv = current_srv.split('.')[0]
        current_srv_port = current_port + '@' + current_srv
        return current_port, current_srv, current_srv_port

    def log_slicer(self):
        # 将log 切片
        with open(self.file, 'r') as fp:
            _data = fp.read()
            fp.close()
        _data = _data.split(self.keyword)
        return _data

    def static_file_dir(self, file_path):
        parent_dir = os.path.abspath(
            os.path.dirname(os.path.dirname(__file__)))
        static_file_path = os.path.join(
            parent_dir, file_path)
        return static_file_path

    def load_server_status(self, filename):
        # 读取由watchedlist 页面写入的json 文件
        with open(filename, 'r') as fp:
            server_status = fp.read()
            server_status = home_page_schema.loads(server_status)
            fp.close()
        return server_status

    def upload_server_status(self, filename, server_update_status):
        # 更新watchedlist 传入的json 文件的status 和version 两个field
        with open(filename, 'w') as fp:
            server_status = home_page_schema.dumps(server_update_status)
            fp.write(server_status)
            fp.close()

    def Srv_features_info_create(self, server_feature_file, server_features_content):
        # 读取log, 直接刷新整个文件
        with open(server_feature_file, 'w') as fp:
            server_features_content = srv_features_schema.dumps(
                server_features_content)
            fp.write(server_features_content)
            fp.close()

    def log_parser(self):
        # 日志分析抓取有用信息
        _data = self.log_slicer()
        current_port, current_srv, current_srv_port = self.server_info_parser()
        for line in _data[0].splitlines():
            groups = re.match(self._lmstat_time_pattern, line)
            # 抓取日志时间
            if groups:
                current_date = date(int(groups.group('year')), int(
                    groups.group('mon')), int(groups.group('day')))
                current_time = time(int(groups.group('hour')),
                                    int(groups.group('min')), 0).strftime("%H:%M:%S")
                continue
            groups = re.match(self._lmstat_server_status, line)
            # 更新home page 的服务器状态、版本和更新时间
            _srv_status_file = self.static_file_dir(
                'static/server_info/home_page.json')
            if groups:
                server_status = self.load_server_status(_srv_status_file)
                for item in server_status:
                    if item['portnumber'] == int(current_port) and item['server'] == current_srv:
                        item['status'] = groups.group('status')
                        item['version'] = groups.group('version')
                        item['update_time'] = str(
                            current_date) + '  ' + str(current_time)
                self.upload_server_status(_srv_status_file, server_status)
                continue

        features = []
        for sections in _data[1:]:
            for line in sections.splitlines():
                groups = re.match(self._lmstat_feature_pattern, line)

                if groups:
                    # 将信息插入数据库
                    current_feature = groups.group('feature')
                    current_total_lic = groups.group('total_lic')
                    current_in_use_lic = groups.group('in_use_lic')
                    self.session.execute(
                        """INSERT INTO
                            lic_usage(server, feature, current_date,
                                      current_time, current_users, total_lic_available)
                            VALUES (?,?,?,?,?,?)""", (current_srv_port,
                                                      current_feature,
                                                      current_date,
                                                      current_time,
                                                      current_in_use_lic,
                                                      current_total_lic))
                    # 将feature 记录到features 列表，后续加载到Srv_feature_info.json 文件中去
                    features.append(current_feature)
                    continue
        # 对SQL 确定insert 动作
        self.conn.commit()
        self.conn.close()
        # 刷新整个Srv_feature_info.json
        server_name = current_port + "@" + current_srv
        server_feature_file = 'static/server_info/' + \
            current_port + '@' + current_srv + '_features.json'
        server_feature_file = self.static_file_dir(server_feature_file)
        Srv_features = {"server": server_name, "features": features}
        self.Srv_features_info_create(server_feature_file, Srv_features)

    def user_checked_info(self):
        _data = self.log_slicer()
        current_status = []
        for section in _data[1:]:
            try:
                groups = re.match(self._lmstat_feature_pattern,
                                  section.splitlines()[0])
                if int(groups.group("in_use_lic")) > 0:
                    for line in section.splitlines():
                        current_feature = {"feature": groups.group("feature")}
                        group_user = re.match(
                            self._lmstat_current_users_1, line)
                        if group_user:
                            current_feature['current_username'] = group_user.group(
                                "User")
                            current_feature['current_client'] = group_user.group(
                                "client")
                            current_feature['current_ip_addr'] = group_user.group(
                                "ip_addr")
                            current_feature['start_date'] = group_user.group(
                                "date")
                            current_feature['start_time'] = group_user.group(
                                "time")
                            current_feature['current_lic_checkedout'] = group_user.group(
                                "lic_checkedout")
                            current_status.append(current_feature)
                        else:
                            group_user = re.match(
                                self._lmstat_current_users_2, line)
                            if group_user:
                                current_feature['current_username'] = group_user.group(
                                    "User")
                                current_feature['current_client'] = group_user.group(
                                    "client")
                                current_feature['current_ip_addr'] = group_user.group(
                                    "ip_addr")
                                current_feature['start_date'] = group_user.group(
                                    "date")
                                current_feature['start_time'] = group_user.group(
                                    "time")
                                current_feature['current_lic_checkedout'] = "1"
                                current_status.append(current_feature)
            except Exception:
                continue
        return current_status


if __name__ == "__main__":
    parent_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    log_file = os.path.join(
        parent_dir, 'static/server_info/5280@SZ-glblic01.log')
    log_parser = log_parser(log_file, 'Users')
    # log_parser.log_parser()
    # log_parser.user_checked_info()
