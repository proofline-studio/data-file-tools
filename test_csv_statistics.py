"""Executable pytest tests shipped with the offline source utility."""
import csv
import io
import json
from fractions import Fraction
import pytest
import csv_statistics as stats


def records(result):
    return list(csv.DictReader(io.StringIO(result['csv'])))


def test_complete_imputation_detection_and_scaling():
    data = 'id,x,comment\n001,0,"a,b"\n002,1,c\n003,2,d\n004,3,e\n005,4,f\n006,100,g\n007,,h\n'
    result = stats.clean_csv(data, ['x'])
    assert [row['x'] for row in records(result)] == ['0', '0.01', '0.02', '0.03', '0.04', '1', '0.025']
    assert records(result)[0] == {'id': '001', 'x': '0', 'comment': 'a,b'}
    assert result['audit']['columns']['x'] == {
        'observed': 6, 'imputed': 1, 'outliers': 1, 'median': '2.5',
        'q1': '1.25', 'q3': '3.75', 'iqr': '2.5', 'lower_fence': '-2.5',
        'upper_fence': '7.5', 'minimum': '0', 'maximum': '100', 'constant': False}
    assert result['audit']['outliers'] == [{'row': 6, 'column': 'x', 'original': '100', 'value': '100'}]
    assert result['audit']['rows_read'] == result['audit']['rows_written'] == 7


def test_multiple_columns_and_constant_policy():
    result = stats.clean_csv('a,b,name\n5,-2,Zoë\n,0,001\n5,2,  raw  \n', ['a', 'b'])
    assert [row['a'] for row in records(result)] == ['0', '0', '0']
    assert [row['b'] for row in records(result)] == ['0', '0.5', '1']
    assert [row['name'] for row in records(result)] == ['Zoë', '001', '  raw  ']
    assert result['audit']['columns']['a']['constant'] is True


def test_exact_decimal_arithmetic_ignores_global_decimal_context():
    from decimal import localcontext
    with localcontext() as context:
        context.prec = 3
        result = stats.clean_csv('x\n0.0000000000000000000000000001\n0.0000000000000000000000000002\n0.0000000000000000000000000004\n', ['x'])
    assert [row['x'] for row in records(result)] == ['0', '0.333333333333', '1']
    assert result['audit']['columns']['x']['median'] == '0.0000000000000000000000000002'


def test_type7_quantiles_and_strict_fences():
    assert stats.quantile([Fraction(1), Fraction(2)], Fraction(1, 4)) == Fraction(5, 4)
    result = stats.clean_csv('x\n0\n0\n0\n0\n1\n', ['x'])
    assert result['audit']['columns']['x']['iqr'] == '0'
    assert result['audit']['columns']['x']['outliers'] == 1


def test_header_only_and_one_value():
    empty = stats.clean_csv('id,x\n', ['x'])
    assert empty['csv'] == 'id,x\n'
    assert empty['audit']['rows_written'] == 0
    assert empty['audit']['columns']['x']['median'] is None
    assert records(stats.clean_csv('x\n3\n', ['x'])) == [{'x': '0'}]


@pytest.mark.parametrize('raw', [
    '', 'x,x\n1,2\n', ',x\n1,2\n', 'x\n1,2\n', 'x\n"unterminated',
    'x\nNaN\n', 'x\nInfinity\n', 'x\n1e999\n', 'x\nNA\n', 'x\nnull\n',
    'x\n1_000\n', 'x\n,\n', 'x\n" "\n', 'x\n' + '1' * 29 + '\n',
    'x\n\0\n'])
def test_invalid_input_is_not_silently_coerced(raw):
    with pytest.raises(ValueError):
        stats.clean_csv(raw, ['x'])


@pytest.mark.parametrize('columns', [[], ['x', 'x'], ['missing'], 'x', [True], [None]])
def test_explicit_unique_present_numeric_columns_required(columns):
    with pytest.raises(ValueError):
        stats.clean_csv('x\n1\n', columns)


def test_csv_field_size_global_is_restored_on_success_and_error():
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(10)
        result = stats.clean_csv('x,label\n1,' + 'v' * 40 + '\n', ['x'])
        assert 'v' * 40 in result['csv']
        assert csv.field_size_limit() == 10
        with pytest.raises(ValueError):
            stats.clean_csv('x,label\n1,' + 'v' * (stats.MAX_FIELD_CHARS + 1) + '\n', ['x'])
        assert csv.field_size_limit() == 10
    finally:
        csv.field_size_limit(previous)


@pytest.mark.parametrize('attribute,limit,data', [
    ('MAX_INPUT_BYTES', 2, 'x\n1\n'), ('MAX_ROWS', 1, 'x\n1\n2\n'),
    ('MAX_COLUMNS', 1, 'x,y\n1,2\n'), ('MAX_CELLS', 1, 'x,y\n1,2\n'),
    ('MAX_OUTPUT_BYTES', 2, 'x\n1\n'), ('MAX_OUTLIERS', 0, 'x\n0\n0\n0\n0\n1\n')])
def test_resource_bounds_fail_without_partial_result(monkeypatch, attribute, limit, data):
    monkeypatch.setattr(stats, attribute, limit)
    with pytest.raises(ValueError):
        stats.clean_csv(data, ['x'])


def test_real_cli_outputs_and_refuses_overwrite(tmp_path, capsys):
    source, output, audit = [tmp_path / name for name in ('input.csv', 'clean.csv', 'audit.json')]
    source.write_text('x,label\n0,001\n2,002\n,003\n', encoding='utf-8')
    argv = [str(source), str(output), '--columns', 'x', '--audit', str(audit)]
    stats.main(argv)
    assert output.read_text() == 'x,label\n0,001\n1,002\n0.5,003\n'
    assert json.loads(audit.read_text())['columns']['x']['imputed'] == 1
    assert json.loads(capsys.readouterr().out)['rows_written'] == 3
    before = output.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        stats.main(argv)
    assert output.read_bytes() == before


def test_cli_validates_before_creating_any_output(tmp_path):
    source, output, audit = [tmp_path / name for name in ('input.csv', 'clean.csv', 'audit.json')]
    source.write_text('x\nNaN\n', encoding='utf-8')
    with pytest.raises(ValueError):
        stats.main([str(source), str(output), '--columns', 'x', '--audit', str(audit)])
    assert not output.exists() and not audit.exists()


def test_quoted_newline_and_outlier_row_identity():
    result = stats.clean_csv('x,note\n0,"line1\nline2"\n0,b\n0,c\n0,d\n1,e\n', ['x'])
    assert records(result)[0]['note'] == 'line1\nline2'
    assert result['audit']['outliers'][0]['row'] == 5


def test_exact_indented_audit_limit_is_enforced(monkeypatch):
    result = stats.clean_csv('x\n1\n2\n', ['x'])
    length = len(stats.serialize_audit(result['audit']).encode('utf-8'))
    assert length > len(json.dumps(result['audit']).encode('utf-8'))
    monkeypatch.setattr(stats, 'MAX_OUTPUT_BYTES', length - 1)
    with pytest.raises(ValueError, match='Audit output limit'):
        stats.clean_csv('x\n1\n2\n', ['x'])
