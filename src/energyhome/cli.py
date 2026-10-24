import argparse
def build_parser()->argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='energyhome',
    description='Energy Consumption Analysis System EnergyHome')
    subparsers = parser.add_subparsers(required=True,dest='command')
    report_parser = subparsers.add_parser('report',help='')
    report_parser.add_argument('--input',type=str,required=True,help='')
    report_parser.add_argument('--tariff',type=str,
    choices=['single','two_zone','three_zone'],default='single',help='')
    return parser

def command_report(args):
    print('Got the report')
    print(args.input)
    print(args.tariff)
    return 0

def main(argv:list[str]|None=None)->int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        if isinstance(e.code,int):
            return e.code
        return 1
    if args.command == 'report':
        return command_report(args)
    return 1
